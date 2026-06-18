from __future__ import annotations

import asyncio
import math
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from memoryos_core.models import (
    ACTIVE_STATUS,
    DELETED_STATUS,
    MemoryItem,
    RawMessage,
    ScopeRule,
    dumps_json,
    loads_json,
    new_id,
    now_ms,
)


SCHEMA_VERSION = 1


class SQLiteMemoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self.fts_enabled = False

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    async def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    async def upsert_memory(self, memory: MemoryItem) -> None:
        await self._run(self._upsert_memory_sync, memory)

    async def get_memory(self, memory_id: str) -> Optional[MemoryItem]:
        return await self._run(self._get_memory_sync, memory_id)

    async def list_memories(
        self,
        allowed_scopes: Optional[Iterable[ScopeRule]] = None,
        status: str = ACTIVE_STATUS,
        limit: int = 50,
        offset: int = 0,
        memory_type: str = "",
        query: str = "",
    ) -> List[MemoryItem]:
        return await self._run(
            self._list_memories_sync,
            list(allowed_scopes or []),
            status,
            limit,
            offset,
            memory_type,
            query,
        )

    async def soft_delete_memory(self, memory_id: str) -> bool:
        return await self._run(self._set_status_sync, memory_id, DELETED_STATUS)

    async def set_memory_status(self, memory_id: str, status: str) -> bool:
        return await self._run(self._set_status_sync, memory_id, status)

    async def update_memory_content(
        self,
        memory_id: str,
        content: str,
        canonical_text: str,
        tags: Sequence[str],
        importance: float,
        confidence: float,
    ) -> bool:
        return await self._run(
            self._update_memory_content_sync,
            memory_id,
            content,
            canonical_text,
            list(tags),
            importance,
            confidence,
        )

    async def append_raw_message(self, raw: RawMessage) -> None:
        await self._run(self._append_raw_message_sync, raw)

    async def append_raw_turn(
        self,
        identity: Any,
        user_text: str,
        assistant_text: str,
    ) -> List[str]:
        ids = []
        if user_text:
            ids.append(identity.message_id or new_id("raw"))
            await self.append_raw_message(
                RawMessage(
                    message_id=ids[-1],
                    platform_id=identity.platform_id,
                    bot_id=identity.bot_id,
                    user_id=identity.user_id,
                    group_id=identity.group_id,
                    session_id=identity.session_id,
                    persona_id=identity.persona_id,
                    role="user",
                    content=user_text,
                    message_type="text",
                    timestamp=identity.timestamp,
                )
            )
        if assistant_text:
            ids.append(new_id("raw"))
            await self.append_raw_message(
                RawMessage(
                    message_id=ids[-1],
                    platform_id=identity.platform_id,
                    bot_id=identity.bot_id,
                    user_id=identity.user_id,
                    group_id=identity.group_id,
                    session_id=identity.session_id,
                    persona_id=identity.persona_id,
                    role="assistant",
                    content=assistant_text,
                    message_type="text",
                    timestamp=now_ms(),
                )
            )
        return ids

    async def recent_raw_messages(
        self, session_id: str, limit: int = 20
    ) -> List[RawMessage]:
        return await self._run(self._recent_raw_messages_sync, session_id, limit)

    async def mark_raw_processed(self, message_ids: Sequence[str]) -> None:
        await self._run(self._mark_raw_processed_sync, list(message_ids))

    async def keyword_search(
        self,
        query: str,
        allowed_scopes: Iterable[ScopeRule],
        top_k: int,
    ) -> List[Tuple[str, float]]:
        return await self._run(
            self._keyword_search_sync, query, list(allowed_scopes), top_k
        )

    async def upsert_vector(self, memory: MemoryItem, vector: Sequence[float]) -> None:
        await self._run(self._upsert_vector_sync, memory, list(vector))

    async def delete_vector(self, memory_id: str) -> None:
        await self._run(self._delete_vector_sync, memory_id)

    async def vector_search(
        self,
        query_vector: Sequence[float],
        allowed_scopes: Iterable[ScopeRule],
        top_k: int,
    ) -> List[Tuple[str, float]]:
        return await self._run(
            self._vector_search_sync, list(query_vector), list(allowed_scopes), top_k
        )

    async def record_access(
        self,
        memory_id: str,
        request_id: str,
        session_id: str,
        used_in_prompt: bool,
        score: float,
    ) -> None:
        await self._run(
            self._record_access_sync,
            memory_id,
            request_id,
            session_id,
            used_in_prompt,
            score,
        )

    async def access_logs(self, memory_id: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        return await self._run(self._access_logs_sync, memory_id, limit)

    async def create_job(self, job_type: str, payload: Dict[str, Any]) -> str:
        return await self._run(self._create_job_sync, job_type, payload)

    async def update_job(
        self, job_id: str, status: str, result: Optional[Dict[str, Any]] = None
    ) -> None:
        await self._run(self._update_job_sync, job_id, status, result or {})

    async def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        return await self._run(self._list_jobs_sync, limit)

    async def export_json(self, include_raw: bool = False) -> Dict[str, Any]:
        return await self._run(self._export_json_sync, include_raw)

    async def import_json(self, payload: Dict[str, Any]) -> Dict[str, int]:
        return await self._run(self._import_json_sync, payload)

    async def stats(self) -> Dict[str, Any]:
        return await self._run(self._stats_sync)

    async def _run(self, func: Any, *args: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._locked_call, func, args)

    def _locked_call(self, func: Any, args: Tuple[Any, ...]) -> Any:
        with self._lock:
            return func(*args)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteMemoryStore is not initialized")
        return self._conn

    def _migrate(self) -> None:
        conn = self.conn
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_items (
                memory_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                owner_key TEXT NOT NULL,
                visibility TEXT NOT NULL,
                platform_id TEXT,
                bot_id TEXT,
                user_id TEXT,
                group_id TEXT,
                session_id TEXT,
                persona_id TEXT,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                canonical_text TEXT,
                entities_json TEXT,
                tags_json TEXT,
                source_message_ids_json TEXT,
                source_summary TEXT,
                confidence REAL DEFAULT 0.8,
                importance REAL DEFAULT 0.5,
                sensitivity TEXT DEFAULT 'normal',
                status TEXT DEFAULT 'active',
                valid_from INTEGER,
                valid_to INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_accessed_at INTEGER,
                access_count INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_scope_owner ON memory_items(scope, owner_key, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_user_group ON memory_items(user_id, group_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_updated ON memory_items(updated_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_messages (
                message_id TEXT PRIMARY KEY,
                platform_id TEXT,
                bot_id TEXT,
                user_id TEXT,
                group_id TEXT,
                session_id TEXT,
                persona_id TEXT,
                role TEXT,
                content TEXT,
                message_type TEXT,
                timestamp INTEGER,
                processed_for_memory INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_session ON raw_messages(session_id, timestamp)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_edges (
                edge_id TEXT PRIMARY KEY,
                source_memory_id TEXT,
                target_memory_id TEXT,
                relation_type TEXT,
                confidence REAL,
                created_at INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT,
                request_id TEXT,
                session_id TEXT,
                used_in_prompt INTEGER,
                score REAL,
                timestamp INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_vectors (
                memory_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                owner_key TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                dim INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scope_grants (
                grant_id TEXT PRIMARY KEY,
                source_scope TEXT NOT NULL,
                source_owner_key TEXT NOT NULL,
                target_scope TEXT NOT NULL,
                target_owner_key TEXT NOT NULL,
                granted_by TEXT,
                expires_at INTEGER,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT,
                result_json TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self._create_fts_table()
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, now_ms()),
        )
        conn.commit()

    def _create_fts_table(self) -> None:
        try:
            self.conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(memory_id UNINDEXED, content, canonical_text)
                """
            )
            self.fts_enabled = True
        except sqlite3.OperationalError:
            self.fts_enabled = False

    def _upsert_memory_sync(self, memory: MemoryItem) -> None:
        self.conn.execute(
            """
            INSERT INTO memory_items (
                memory_id, scope, owner_key, visibility, platform_id, bot_id,
                user_id, group_id, session_id, persona_id, memory_type, content,
                canonical_text, entities_json, tags_json, source_message_ids_json,
                source_summary, confidence, importance, sensitivity, status,
                valid_from, valid_to, created_at, updated_at, last_accessed_at,
                access_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                scope=excluded.scope,
                owner_key=excluded.owner_key,
                visibility=excluded.visibility,
                platform_id=excluded.platform_id,
                bot_id=excluded.bot_id,
                user_id=excluded.user_id,
                group_id=excluded.group_id,
                session_id=excluded.session_id,
                persona_id=excluded.persona_id,
                memory_type=excluded.memory_type,
                content=excluded.content,
                canonical_text=excluded.canonical_text,
                entities_json=excluded.entities_json,
                tags_json=excluded.tags_json,
                source_message_ids_json=excluded.source_message_ids_json,
                source_summary=excluded.source_summary,
                confidence=excluded.confidence,
                importance=excluded.importance,
                sensitivity=excluded.sensitivity,
                status=excluded.status,
                valid_from=excluded.valid_from,
                valid_to=excluded.valid_to,
                updated_at=excluded.updated_at
            """,
            self._memory_values(memory),
        )
        self._upsert_fts(memory)
        self.conn.commit()

    def _memory_values(self, memory: MemoryItem) -> Tuple[Any, ...]:
        return (
            memory.memory_id,
            memory.scope,
            memory.owner_key,
            memory.visibility,
            memory.platform_id,
            memory.bot_id,
            memory.user_id,
            memory.group_id,
            memory.session_id,
            memory.persona_id,
            memory.memory_type,
            memory.content,
            memory.canonical_text,
            dumps_json(memory.entities),
            dumps_json(memory.tags),
            dumps_json(memory.source_message_ids),
            memory.source_summary,
            memory.confidence,
            memory.importance,
            memory.sensitivity,
            memory.status,
            memory.valid_from,
            memory.valid_to,
            memory.created_at,
            memory.updated_at,
            memory.last_accessed_at,
            memory.access_count,
        )

    def _upsert_fts(self, memory: MemoryItem) -> None:
        if not self.fts_enabled:
            return
        self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory.memory_id,))
        if memory.status == ACTIVE_STATUS:
            self.conn.execute(
                "INSERT INTO memory_fts(memory_id, content, canonical_text) VALUES (?, ?, ?)",
                (memory.memory_id, memory.content, memory.canonical_text or ""),
            )

    def _get_memory_sync(self, memory_id: str) -> Optional[MemoryItem]:
        row = self.conn.execute(
            "SELECT * FROM memory_items WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return None
        return MemoryItem.from_row(dict(row))

    def _list_memories_sync(
        self,
        rules: List[ScopeRule],
        status: str,
        limit: int,
        offset: int,
        memory_type: str,
        query: str,
    ) -> List[MemoryItem]:
        clauses = ["status = ?"]
        params: List[Any] = [status]
        if rules:
            scope_clause, scope_params = _scope_sql(rules)
            clauses.append(scope_clause)
            params.extend(scope_params)
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if query:
            clauses.append("(content LIKE ? OR canonical_text LIKE ? OR tags_json LIKE ?)")
            like = "%%%s%%" % query
            params.extend([like, like, like])
        params.extend([int(limit), int(offset)])
        rows = self.conn.execute(
            """
            SELECT m.* FROM memory_items m
            WHERE %s
            ORDER BY importance DESC, updated_at DESC
            LIMIT ? OFFSET ?
            """
            % " AND ".join(clauses),
            params,
        ).fetchall()
        return [MemoryItem.from_row(dict(row)) for row in rows]

    def _set_status_sync(self, memory_id: str, status: str) -> bool:
        cur = self.conn.execute(
            "UPDATE memory_items SET status = ?, updated_at = ? WHERE memory_id = ?",
            (status, now_ms(), memory_id),
        )
        if status != ACTIVE_STATUS and self.fts_enabled:
            self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def _update_memory_content_sync(
        self,
        memory_id: str,
        content: str,
        canonical_text: str,
        tags: List[str],
        importance: float,
        confidence: float,
    ) -> bool:
        cur = self.conn.execute(
            """
            UPDATE memory_items
            SET content = ?, canonical_text = ?, tags_json = ?, importance = ?,
                confidence = ?, updated_at = ?
            WHERE memory_id = ?
            """,
            (
                content,
                canonical_text,
                dumps_json(tags),
                importance,
                confidence,
                now_ms(),
                memory_id,
            ),
        )
        memory = self._get_memory_sync(memory_id)
        if memory:
            self._upsert_fts(memory)
        self.conn.commit()
        return cur.rowcount > 0

    def _append_raw_message_sync(self, raw: RawMessage) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO raw_messages (
                message_id, platform_id, bot_id, user_id, group_id, session_id,
                persona_id, role, content, message_type, timestamp, processed_for_memory
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw.message_id,
                raw.platform_id,
                raw.bot_id,
                raw.user_id,
                raw.group_id,
                raw.session_id,
                raw.persona_id,
                raw.role,
                raw.content,
                raw.message_type,
                raw.timestamp,
                raw.processed_for_memory,
            ),
        )
        self.conn.commit()

    def _recent_raw_messages_sync(self, session_id: str, limit: int) -> List[RawMessage]:
        rows = self.conn.execute(
            """
            SELECT * FROM raw_messages
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (session_id, int(limit)),
        ).fetchall()
        result = [RawMessage(**dict(row)) for row in rows]
        result.reverse()
        return result

    def _mark_raw_processed_sync(self, message_ids: List[str]) -> None:
        if not message_ids:
            return
        self.conn.executemany(
            "UPDATE raw_messages SET processed_for_memory = 1 WHERE message_id = ?",
            [(mid,) for mid in message_ids],
        )
        self.conn.commit()

    def _keyword_search_sync(
        self, query: str, rules: List[ScopeRule], top_k: int
    ) -> List[Tuple[str, float]]:
        if not query.strip():
            return []
        scope_clause, scope_params = _scope_sql(rules)
        if self.fts_enabled:
            fts_query = _fts_query(query)
            rows = self.conn.execute(
                """
                SELECT m.memory_id, bm25(memory_fts) AS rank
                FROM memory_fts
                JOIN memory_items m ON m.memory_id = memory_fts.memory_id
                WHERE memory_fts MATCH ? AND m.status = ? AND %s
                ORDER BY rank
                LIMIT ?
                """
                % scope_clause,
                [fts_query, ACTIVE_STATUS] + scope_params + [int(top_k)],
            ).fetchall()
            return [
                (str(row["memory_id"]), max(0.0, 1.0 / (1.0 + abs(float(row["rank"])))))
                for row in rows
            ]
        like = "%%%s%%" % query
        rows = self.conn.execute(
            """
            SELECT memory_id, content, canonical_text
            FROM memory_items
            WHERE status = ? AND %s AND (content LIKE ? OR canonical_text LIKE ?)
            ORDER BY updated_at DESC
            LIMIT ?
            """
            % scope_clause,
            [ACTIVE_STATUS] + scope_params + [like, like, int(top_k)],
        ).fetchall()
        return [(str(row["memory_id"]), 0.5) for row in rows]

    def _upsert_vector_sync(self, memory: MemoryItem, vector: List[float]) -> None:
        if not vector:
            return
        self.conn.execute(
            """
            INSERT INTO memory_vectors(memory_id, scope, owner_key, vector_json, dim, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                scope=excluded.scope,
                owner_key=excluded.owner_key,
                vector_json=excluded.vector_json,
                dim=excluded.dim,
                updated_at=excluded.updated_at
            """,
            (
                memory.memory_id,
                memory.scope,
                memory.owner_key,
                dumps_json(vector),
                len(vector),
                now_ms(),
            ),
        )
        self.conn.commit()

    def _delete_vector_sync(self, memory_id: str) -> None:
        self.conn.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory_id,))
        self.conn.commit()

    def _vector_search_sync(
        self, query_vector: List[float], rules: List[ScopeRule], top_k: int
    ) -> List[Tuple[str, float]]:
        if not query_vector:
            return []
        scope_clause, scope_params = _scope_sql(rules)
        rows = self.conn.execute(
            """
            SELECT v.memory_id, v.vector_json
            FROM memory_vectors v
            JOIN memory_items m ON m.memory_id = v.memory_id
            WHERE m.status = ? AND %s
            """
            % scope_clause,
            [ACTIVE_STATUS] + scope_params,
        ).fetchall()
        scored = []
        for row in rows:
            vector = loads_json(row["vector_json"], [])
            score = cosine_similarity(query_vector, vector)
            if score > 0:
                scored.append((str(row["memory_id"]), score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: int(top_k)]

    def _record_access_sync(
        self,
        memory_id: str,
        request_id: str,
        session_id: str,
        used_in_prompt: bool,
        score: float,
    ) -> None:
        ts = now_ms()
        self.conn.execute(
            """
            INSERT INTO memory_access_log(memory_id, request_id, session_id, used_in_prompt, score, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (memory_id, request_id, session_id, 1 if used_in_prompt else 0, score, ts),
        )
        self.conn.execute(
            """
            UPDATE memory_items
            SET last_accessed_at = ?, access_count = COALESCE(access_count, 0) + 1
            WHERE memory_id = ?
            """,
            (ts, memory_id),
        )
        self.conn.commit()

    def _access_logs_sync(self, memory_id: str, limit: int) -> List[Dict[str, Any]]:
        params: List[Any] = []
        clause = ""
        if memory_id:
            clause = "WHERE memory_id = ?"
            params.append(memory_id)
        params.append(int(limit))
        rows = self.conn.execute(
            """
            SELECT * FROM memory_access_log
            %s
            ORDER BY timestamp DESC
            LIMIT ?
            """
            % clause,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def _create_job_sync(self, job_type: str, payload: Dict[str, Any]) -> str:
        job_id = new_id("job")
        ts = now_ms()
        self.conn.execute(
            """
            INSERT INTO memory_jobs(job_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, job_type, "queued", dumps_json(payload), "{}", ts, ts),
        )
        self.conn.commit()
        return job_id

    def _update_job_sync(
        self, job_id: str, status: str, result: Dict[str, Any]
    ) -> None:
        self.conn.execute(
            "UPDATE memory_jobs SET status = ?, result_json = ?, updated_at = ? WHERE job_id = ?",
            (status, dumps_json(result), now_ms(), job_id),
        )
        self.conn.commit()

    def _list_jobs_sync(self, limit: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM memory_jobs ORDER BY created_at DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def _export_json_sync(self, include_raw: bool) -> Dict[str, Any]:
        memories = [
            MemoryItem.from_row(dict(row)).to_dict()
            for row in self.conn.execute("SELECT * FROM memory_items").fetchall()
        ]
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "exported_at": now_ms(),
            "memories": memories,
            "edges": [
                dict(row)
                for row in self.conn.execute("SELECT * FROM memory_edges").fetchall()
            ],
        }
        if include_raw:
            payload["raw_messages"] = [
                dict(row)
                for row in self.conn.execute("SELECT * FROM raw_messages").fetchall()
            ]
        return payload

    def _import_json_sync(self, payload: Dict[str, Any]) -> Dict[str, int]:
        counts = {"memories": 0, "raw_messages": 0, "edges": 0}
        for item in payload.get("memories", []) or []:
            memory = MemoryItem(**item)
            self._upsert_memory_sync(memory)
            counts["memories"] += 1
        for raw in payload.get("raw_messages", []) or []:
            self._append_raw_message_sync(RawMessage(**raw))
            counts["raw_messages"] += 1
        for edge in payload.get("edges", []) or []:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO memory_edges(edge_id, source_memory_id, target_memory_id, relation_type, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.get("edge_id") or new_id("edge"),
                    edge.get("source_memory_id"),
                    edge.get("target_memory_id"),
                    edge.get("relation_type"),
                    edge.get("confidence", 0.5),
                    edge.get("created_at") or now_ms(),
                ),
            )
            counts["edges"] += 1
        self.conn.commit()
        return counts

    def _stats_sync(self) -> Dict[str, Any]:
        active_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM memory_items WHERE status = ?", (ACTIVE_STATUS,)
        ).fetchone()["c"]
        total_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM memory_items"
        ).fetchone()["c"]
        vector_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM memory_vectors"
        ).fetchone()["c"]
        raw_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM raw_messages"
        ).fetchone()["c"]
        return {
            "db_path": str(self.db_path),
            "schema_version": SCHEMA_VERSION,
            "fts_enabled": self.fts_enabled,
            "active_memories": active_count,
            "total_memories": total_count,
            "vectors": vector_count,
            "raw_messages": raw_count,
        }


class SQLiteVectorStore:
    def __init__(self, store: SQLiteMemoryStore) -> None:
        self.store = store

    async def upsert(self, memory_id: str, vector: Sequence[float]) -> None:
        memory = await self.store.get_memory(memory_id)
        if memory:
            await self.store.upsert_vector(memory, vector)

    async def delete(self, memory_id: str) -> None:
        await self.store.delete_vector(memory_id)

    async def search(
        self,
        query_vector: Sequence[float],
        allowed_scopes: Iterable[ScopeRule],
        top_k: int,
    ) -> List[Tuple[str, float]]:
        return await self.store.vector_search(query_vector, allowed_scopes, top_k)

    async def rebuild(self, embeddings: Iterable[Tuple[str, Sequence[float]]]) -> int:
        count = 0
        for memory_id, vector in embeddings:
            await self.upsert(memory_id, vector)
            count += 1
        return count


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
    norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def _scope_sql(rules: List[ScopeRule]) -> Tuple[str, List[Any]]:
    if not rules:
        return "1 = 1", []
    parts = []
    params: List[Any] = []
    for rule in rules:
        parts.append("(m.scope = ? AND m.owner_key = ?)")
        params.extend([rule.scope, rule.owner_key])
    return "(" + " OR ".join(parts) + ")", params


def _fts_query(query: str) -> str:
    tokens = [tok.strip() for tok in query.replace('"', " ").split() if tok.strip()]
    if not tokens:
        return '""'
    return " OR ".join('"%s"' % tok.replace('"', "") for tok in tokens[:12])


def _decode_json_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    for key in list(row.keys()):
        if key.endswith("_json"):
            row[key[:-5]] = loads_json(row.pop(key), {})
    return row

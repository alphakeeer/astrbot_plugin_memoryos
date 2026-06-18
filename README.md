# AstrBot MemoryOS

MemoryOS is a structured long-term memory plugin for AstrBot. It is designed for private and group chats, with strict scope isolation, auditable storage, retrieval gating, and temporary dynamic context injection.

## Features

- Structured memory items instead of append-only chat summaries.
- Private chat, group shared, user-in-group, session, persona, and global scopes.
- Safe defaults: private memories are not used in groups, and group memories are not used in private chats.
- SQLite metadata, FTS5 keyword search, and local vector table by default.
- Embedding provider integration when configured; keyword-only fallback when not configured.
- Explicit memory commands, conservative automatic extraction, conflict handling, access logs, export/import, and index rebuild.
- WebUI Page for browsing, searching, editing, deleting, importing, exporting, and rebuilding memories.

## Install

Place this repository directory under:

```text
AstrBot/data/plugins/astrbot_plugin_memoryos
```

The plugin stores runtime data in:

```text
AstrBot/data/plugin_data/astrbot_plugin_memoryos/
```

No third-party runtime dependency is required for the default SQLite backend.

## Commands

```text
/mem remember <content>
/mem search <query>
/mem list
/mem forget <memory_id>
/mem forget all
/mem summarize
/mem status
/mem on
/mem off
/mem export
/mem import <json>
/mem rebuild-index
/mem group on
/mem group off
/mem group remember <content>
/mem group list
/mem group forget <memory_id>
/mem group policy conservative|normal|aggressive
```

Group administration commands check AstrBot's `event.is_admin()` / role field when available.

## Privacy Defaults

- Private chat retrieval allows only `user_private`, `session`, and `persona` memories.
- Group chat retrieval allows only current `group_shared`, current speaker `user_in_group`, `session`, and `persona` memories.
- `allow_private_memory_in_group` and `allow_group_memory_in_private` are disabled by default.
- Dynamic memory context is injected with `extra_user_content_parts` and `mark_as_temp()`, not appended to `system_prompt`.

## Development

Run local checks:

```bash
python3 -m compileall .
python3 -m pytest
python3 -m ruff check .
```

The current local machine may use Python 3.9, while recent AstrBot releases may require a newer Python runtime. Core modules are kept Python 3.9-compatible for tests.


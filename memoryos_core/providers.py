from __future__ import annotations

import hashlib
import json
from typing import Any, List, Optional, Sequence


class AstrBotAI:
    def __init__(self, context: Any, config: Any) -> None:
        self.context = context
        self.config = config
        self.embedding_available = False
        self.last_embedding_error = ""
        self.last_llm_error = ""

    async def llm_generate(self, event: Any, prompt: str, system_prompt: str = "") -> str:
        provider_id = await self._resolve_llm_provider_id(event)
        if not provider_id:
            return ""
        try:
            if hasattr(self.context, "llm_generate"):
                kwargs = {"chat_provider_id": provider_id, "prompt": prompt}
                if system_prompt:
                    kwargs["system_prompt"] = system_prompt
                resp = await self.context.llm_generate(**kwargs)
                return str(getattr(resp, "completion_text", "") or "")
        except Exception as exc:
            self.last_llm_error = str(exc)
        return ""

    async def embed(self, text: str) -> Optional[List[float]]:
        if not text.strip():
            return None
        provider = await self._resolve_embedding_provider()
        if provider is None:
            self.embedding_available = False
            return None
        try:
            vector = await provider.get_embedding(text)
            if vector:
                self.embedding_available = True
                self.last_embedding_error = ""
                return [float(value) for value in vector]
        except Exception as exc:
            self.embedding_available = False
            self.last_embedding_error = str(exc)
        return None

    async def embed_many(self, texts: Sequence[str]) -> List[Optional[List[float]]]:
        provider = await self._resolve_embedding_provider()
        if provider is None:
            self.embedding_available = False
            return [None for _ in texts]
        try:
            if hasattr(provider, "get_embeddings"):
                vectors = await provider.get_embeddings(list(texts))
                self.embedding_available = True
                return [[float(value) for value in vector] for vector in vectors]
        except Exception as exc:
            self.last_embedding_error = str(exc)
        result = []
        for text in texts:
            result.append(await self.embed(text))
        return result

    async def _resolve_llm_provider_id(self, event: Any) -> str:
        configured = str(getattr(self.config, "llm_provider_id", "") or "")
        if configured:
            return configured
        try:
            if hasattr(self.context, "get_current_chat_provider_id"):
                return str(
                    await self.context.get_current_chat_provider_id(
                        getattr(event, "unified_msg_origin", "")
                    )
                )
        except Exception as exc:
            self.last_llm_error = str(exc)
        return ""

    async def _resolve_embedding_provider(self) -> Any:
        manager = getattr(self.context, "provider_manager", None)
        provider_id = str(getattr(self.config, "embedding_provider_id", "") or "")
        if manager is None:
            return None
        try:
            if provider_id and hasattr(manager, "get_provider_by_id"):
                provider = await manager.get_provider_by_id(provider_id)
                if provider and hasattr(provider, "get_embedding"):
                    return provider
            for provider in getattr(manager, "embedding_provider_insts", []) or []:
                if hasattr(provider, "get_embedding"):
                    return provider
        except Exception as exc:
            self.last_embedding_error = str(exc)
        return None


class DeterministicTestAI:
    """Small deterministic provider used by tests and local smoke checks."""

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim
        self.embedding_available = True
        self.last_embedding_error = ""
        self.last_llm_error = ""

    async def llm_generate(self, event: Any, prompt: str, system_prompt: str = "") -> str:
        return "[]"

    async def embed(self, text: str) -> List[float]:
        return hash_embedding(text, self.dim)

    async def embed_many(self, texts: Sequence[str]) -> List[List[float]]:
        return [hash_embedding(text, self.dim) for text in texts]


def hash_embedding(text: str, dim: int = 32) -> List[float]:
    buckets = [0.0 for _ in range(dim)]
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = digest[0] % dim
        sign = 1.0 if digest[1] % 2 == 0 else -1.0
        buckets[idx] += sign
    return buckets


def extract_json(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        return None
    for candidate in _json_candidates(stripped):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def _json_candidates(text: str) -> List[str]:
    candidates = [text]
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part:
                candidates.append(part)
    start_array = text.find("[")
    end_array = text.rfind("]")
    if start_array >= 0 and end_array > start_array:
        candidates.append(text[start_array : end_array + 1])
    start_obj = text.find("{")
    end_obj = text.rfind("}")
    if start_obj >= 0 and end_obj > start_obj:
        candidates.append(text[start_obj : end_obj + 1])
    return candidates


def _tokens(text: str) -> List[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [token for token in normalized.split() if token]


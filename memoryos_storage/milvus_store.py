from __future__ import annotations


class MilvusVectorStore:
    """Optional adapter placeholder.

    MemoryOS defaults to SQLite vectors to keep plugin installation small and reliable.
    This class exists so deployments can add a Milvus implementation without changing
    the public storage interface.
    """

    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "Milvus backend is optional and not bundled. Use vector_backend=sqlite "
            "or install a custom Milvus adapter."
        )


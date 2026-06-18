from __future__ import annotations


class QdrantVectorStore:
    """Optional adapter placeholder for external Qdrant deployments."""

    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "Qdrant backend is optional and not bundled. Use vector_backend=sqlite "
            "or install a custom Qdrant adapter."
        )


"""Optional local semantic/vector search over indexed knowledge.

Heavy optional dependencies (LanceDB, CLIP, Torch) are imported lazily so that
importing Talos never requires the optional visual-search stack. When those
packages are absent, semantic search is reported as unavailable and callers
receive empty results instead of an import failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)

VECTORS_DIR = Path(__file__).resolve().parents[2] / "state" / "vectors"


class SemanticEngine:
    """Thin wrapper around a LanceDB vector table populated with CLIP embeddings."""

    def __init__(self) -> None:
        self.model = None
        self.preprocess = None
        self.db = None
        self.available = False
        self._load()

    def _load(self) -> None:
        try:
            import lancedb
            from clip import load as load_clip
        except ImportError as error:
            LOGGER.info("Semantic search disabled: %s", error)
            return

        try:
            self.model, self.preprocess = load_clip("ViT-B/32", device="cpu")
            VECTORS_DIR.mkdir(parents=True, exist_ok=True)
            self.db = lancedb.connect(str(VECTORS_DIR))
            self._init_tables()
        except Exception:
            LOGGER.exception("Semantic engine failed to initialise; search disabled")
            self.model = None
            self.db = None
            return
        self.available = self.db is not None

    def _init_tables(self) -> None:
        if self.db is not None and "knowledge_vectors" not in self.db.table_names():
            self.db.create_table(
                "knowledge_vectors",
                schema={"vector": "vector(512)", "text": "text", "store": "text", "id": "text"},
            )

    def embed_text(self, text: str):
        """Return a 512-dimension CLIP embedding for ``text``."""
        if not self.available or self.model is None:
            raise RuntimeError("Semantic search not available - CLIP is not installed")

        import torch

        with torch.no_grad():
            tokens = self.model.tokenize([text])[0]
            embedding = self.model.encode_text(tokens)
            return embedding.cpu().numpy()

    def index_knowledge(self, items: list[dict]) -> None:
        """Index ``{"text", "store", "id"}`` records into the vector store."""
        if not self.available:
            LOGGER.debug("Cannot index knowledge: semantic engine unavailable")
            return

        data = []
        for item in items:
            text = item.get("text", "")
            if not text:
                continue
            data.append(
                {
                    "vector": self.embed_text(text),
                    "text": text,
                    "store": item.get("store", "unknown"),
                    "id": item.get("id", "unknown"),
                }
            )
        if data and self.db is not None:
            self.db.create_table("knowledge_vectors", data, mode="append")

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search for the ``limit`` nearest indexed records to ``query``."""
        if not self.available or self.db is None:
            return []
        table = self.db.open_table("knowledge_vectors")
        return table.search(self.embed_text(query), limit=limit).to_pandas().to_dict("records")


engine = SemanticEngine()

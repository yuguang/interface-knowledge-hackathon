"""Dense retrieval: one Chroma collection, one document per distinct chunk, snapshot membership as metadata.

Chroma stores and embeds; queries are exact cosine over the snapshot's stored embeddings (ruling R39), because
Chroma 1.5.9's HNSW graph differs between builds of the same data and so its approximate neighbours do too.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import numpy as np

from ..store import Store

COLLECTION = "chunks"
BATCH = 1000
for _name in ("chromadb", "onnxruntime"):
    logging.getLogger(_name).setLevel(logging.ERROR)


def embedding_function():
    """Chroma's default ONNX all-MiniLM-L6-v2 (no torch). Tests monkeypatch this."""
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    return DefaultEmbeddingFunction()


def snapshot_flag(snapshot_id: str) -> str:
    """Metadata key for snapshot membership: "01_2026-05-12_baseline" -> "in_01"."""
    return f"in_{snapshot_id[:2]}"


def _drop_cached_client(path: str) -> None:
    """Chroma caches one open system per path in-process; after the files are wiped it would write to a dead DB."""
    try:
        # Private API, verified against chromadb==1.5.9 (pinned in submission/requirements.txt).
        from chromadb.api.shared_system_client import SharedSystemClient
        system = SharedSystemClient._identifier_to_system.pop(path, None)
        if system is not None:
            system.stop()
    except Exception:
        pass


class DenseIndex:
    def __init__(self, index_dir: Path):
        import chromadb
        from chromadb.config import Settings
        path = str(Path(index_dir))
        _drop_cached_client(path)
        shutil.rmtree(path, ignore_errors=True)  # build() keeps index_dir; a stale index must not leak in
        Path(path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
        self.embed = embedding_function()
        self.collection = self.client.create_collection(
            COLLECTION, embedding_function=self.embed, metadata={"hnsw:space": "cosine"})
        self._matrices: dict[str, tuple[np.ndarray, np.ndarray]] = {}  # flag -> (chunk ids, unit-row matrix)

    def index_snapshot(self, store: Store, snapshot_id: str) -> None:
        """Embed chunks first seen in this snapshot; flag recurring chunks with a metadata update."""
        flag = snapshot_flag(snapshot_id)
        self._matrices.pop(flag, None)
        texts = {sc.chunk_id: sc.chunk for sc in store.new_chunks(snapshot_id)}
        all_ids = sorted(store.chunk_ids_in(snapshot_id))
        present = set()
        for i in range(0, len(all_ids), BATCH):
            present.update(int(x) for x in self.collection.get(ids=[str(c) for c in all_ids[i:i + BATCH]],
                                                               include=[])["ids"])
        # Missing = new_chunks, plus (normally none) recurring chunks whose first snapshot was never indexed.
        new = {cid: texts.get(cid) or store.chunk(cid).chunk for cid in all_ids if cid not in present}
        ids = sorted(new)
        for i in range(0, len(ids), BATCH):
            part = ids[i:i + BATCH]
            self.collection.add(ids=[str(c) for c in part], documents=[new[c].text for c in part],
                                metadatas=[{"url": new[c].url, flag: True} for c in part])
        upd = sorted(present)
        for i in range(0, len(upd), BATCH):
            part = upd[i:i + BATCH]
            self.collection.update(ids=[str(c) for c in part], metadatas=[{flag: True}] * len(part))

    def _matrix(self, flag: str) -> tuple[np.ndarray, np.ndarray]:
        """The snapshot's chunk ids (ascending) and L2-normalised float32 embeddings; loaded once, cached."""
        if flag not in self._matrices:
            got = self.collection.get(where={flag: True}, include=["embeddings"])
            ids = np.array([int(i) for i in got["ids"]], dtype=np.int64)
            order = np.argsort(ids, kind="stable")
            if not len(ids):
                self._matrices[flag] = (ids, np.zeros((0, 0), dtype=np.float32))
                return self._matrices[flag]
            m = np.asarray(got["embeddings"], dtype=np.float32).reshape(len(ids), -1)[order]
            norms = np.linalg.norm(m, axis=1, keepdims=True)
            self._matrices[flag] = (ids[order], m / np.where(norms > 0, norms, 1.0))
        return self._matrices[flag]

    def query(self, question: str, snapshot_id: str, k: int) -> list[tuple[int, float]]:
        """(chunk_id, cosine similarity) for the k most similar chunks in the snapshot; exact, ties by chunk_id."""
        if not question.strip():
            return []
        ids, m = self._matrix(snapshot_flag(snapshot_id))
        if not len(ids):
            return []
        q = np.asarray(self.embed([question])[0], dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = m @ q
        top = np.lexsort((ids, -scores))[:k]  # primary -score, then chunk_id
        return [(int(ids[i]), float(scores[i])) for i in top]

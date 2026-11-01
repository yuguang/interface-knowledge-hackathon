"""Dense retrieval: one Chroma collection, one document per distinct chunk, snapshot membership as metadata."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..store import Store

COLLECTION = "chunks"
BATCH = 1000
# HNSW params pinned explicitly (Chroma's defaults, except num_threads, which defaults to cpu_count()).
# NOTE: on chromadb 1.5.9 this does NOT make builds reproducible: num_threads is dropped from the resolved
# config, and two single-threaded builds of the same data still return different neighbours
# (see test_dense_index_is_reproducible, xfail).
HNSW = {"hnsw:space": "cosine", "hnsw:num_threads": 1, "hnsw:construction_ef": 100, "hnsw:M": 16,
        "hnsw:search_ef": 100}
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
        self.collection = self.client.create_collection(
            COLLECTION, embedding_function=embedding_function(), metadata=HNSW)

    def index_snapshot(self, store: Store, snapshot_id: str) -> None:
        """Embed chunks first seen in this snapshot; flag recurring chunks with a metadata update."""
        flag = snapshot_flag(snapshot_id)
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

    def query(self, question: str, snapshot_id: str, k: int) -> list[tuple[int, float]]:
        """(chunk_id, cosine similarity) for the k nearest chunks present in the snapshot."""
        if not question.strip():
            return []
        res = self.collection.query(query_texts=[question], n_results=k, where={snapshot_flag(snapshot_id): True},
                                    include=["distances"])
        out = [(int(i), 1.0 - float(d)) for i, d in zip(res["ids"][0], res["distances"][0])]
        return sorted(out, key=lambda x: (-x[1], x[0]))  # stable rank for equal distances

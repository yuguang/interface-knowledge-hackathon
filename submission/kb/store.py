"""SQLite store: snapshots, pages, deduplicated chunks, facts, resolved facts, answer memo."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Optional
from urllib.parse import urlsplit

from .keys import parse_key
from .model import Chunk, FactObs, Hit, PageInfo, ResolvedFact, StoredChunk

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot (
  snapshot_id TEXT PRIMARY KEY, taken_on TEXT NOT NULL, ord INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS page (
  page_id INTEGER PRIMARY KEY, snapshot_id TEXT, url TEXT, url_key TEXT,
  title TEXT, page_type TEXT, authority INTEGER, page_date TEXT, content_hash TEXT,
  UNIQUE(snapshot_id, url));
CREATE TABLE IF NOT EXISTS chunk (
  chunk_id INTEGER PRIMARY KEY, chunk_hash TEXT UNIQUE,
  url TEXT, title TEXT, heading_path TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS chunk_snapshot (chunk_id INTEGER, snapshot_id TEXT, page_id INTEGER,
  PRIMARY KEY (chunk_id, snapshot_id, page_id));
CREATE INDEX IF NOT EXISTS cs_snap ON chunk_snapshot(snapshot_id);
CREATE TABLE IF NOT EXISTS fact_obs (
  obs_id INTEGER PRIMARY KEY,
  fact_key TEXT NOT NULL, product TEXT, attribute TEXT, qualifiers_json TEXT,
  metric TEXT, value_raw TEXT, value_norm TEXT,
  snapshot_id TEXT, taken_on TEXT, page_id INTEGER, url TEXT,
  page_type TEXT, authority INTEGER, page_date TEXT, quote TEXT, extractor TEXT, chunk_id INTEGER);
CREATE INDEX IF NOT EXISTS fo_key_snap ON fact_obs(fact_key, snapshot_id);
CREATE INDEX IF NOT EXISTS fo_prod_attr ON fact_obs(product, attribute, snapshot_id);
CREATE TABLE IF NOT EXISTS fact_resolved (
  fact_key TEXT, snapshot_id TEXT, value_norm TEXT, obs_id INTEGER, losers_json TEXT,
  winner_json TEXT, rule TEXT,
  PRIMARY KEY (fact_key, snapshot_id));
CREATE TABLE IF NOT EXISTS page_link (
  snapshot_id TEXT, url TEXT, ord INTEGER, text TEXT, href TEXT, PRIMARY KEY (snapshot_id, url, text, href));
CREATE TABLE IF NOT EXISTS answer_memo (
  intent_key TEXT, snapshot_id TEXT, answer_json TEXT, PRIMARY KEY (intent_key, snapshot_id));
"""

_OBS_COLS = ("fact_key", "product", "attribute", "value_raw", "value_norm", "snapshot_id", "taken_on", "url",
             "page_type", "authority", "page_date", "quote", "extractor", "chunk_id")


def _obs_from_row(r) -> FactObs:
    d = {c: r[c] for c in _OBS_COLS}
    return FactObs(qualifiers=json.loads(r["qualifiers_json"]), **d)


class Store:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        try:
            self._db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts '
                             'USING fts5(text, tokenize="porter unicode61")')
            self.fts_available = True
        except sqlite3.OperationalError:
            self.fts_available = False

    # snapshots / pages / chunks
    def add_snapshot(self, snapshot_id: str, taken_on: str) -> None:
        n = self._db.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0]
        self._db.execute("INSERT OR REPLACE INTO snapshot VALUES (?,?,?)", (snapshot_id, taken_on, n))
        self._db.commit()

    def add_page(self, snapshot_id: str, url: str, title: str, info: PageInfo, content_hash: str) -> int:
        sp = urlsplit(url)
        url_key = (sp.netloc + sp.path).lower().rstrip("/")
        self._db.execute(
            "INSERT INTO page (snapshot_id,url,url_key,title,page_type,authority,page_date,content_hash) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(snapshot_id,url) DO UPDATE SET "
            "title=excluded.title,page_type=excluded.page_type,authority=excluded.authority,"
            "page_date=excluded.page_date,content_hash=excluded.content_hash",
            (snapshot_id, url, url_key, title, info.page_type, info.authority, info.page_date, content_hash))
        pid = self._db.execute("SELECT page_id FROM page WHERE snapshot_id=? AND url=?", (snapshot_id, url)).fetchone()[0]
        self._db.commit()
        return pid

    def page_dates(self, snapshot_id: str, urls: list[str]) -> dict[str, Optional[str]]:
        out: dict[str, Optional[str]] = {}
        for u in urls:
            r = self._db.execute("SELECT page_date FROM page WHERE snapshot_id=? AND url=?", (snapshot_id, u)).fetchone()
            out[u] = r[0] if r else None
        return out

    def add_links(self, snapshot_id: str, url: str, links: list[tuple[str, str]]) -> None:
        """A page's (link text, target) pairs; duplicates are ignored, first-seen order kept."""
        n = self._db.execute("SELECT COUNT(*) FROM page_link WHERE snapshot_id=? AND url=?",
                             (snapshot_id, url)).fetchone()[0]
        self._db.executemany("INSERT OR IGNORE INTO page_link VALUES (?,?,?,?,?)",
                             [(snapshot_id, url, n + i, t, h) for i, (t, h) in enumerate(links)])
        self._db.commit()

    def links(self, snapshot_id: str, url: str) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self._db.execute(
            "SELECT text, href FROM page_link WHERE snapshot_id=? AND url=? ORDER BY ord", (snapshot_id, url))]

    def add_chunks(self, snapshot_id: str, page_id: int, chunks: list[Chunk]) -> list[StoredChunk]:
        out = []
        for c in chunks:
            row = self._db.execute("SELECT chunk_id FROM chunk WHERE chunk_hash=?", (c.hash,)).fetchone()
            if row:
                cid = row[0]
            else:
                cid = self._db.execute(
                    "INSERT INTO chunk (chunk_hash,url,title,heading_path,text) VALUES (?,?,?,?,?)",
                    (c.hash, c.url, c.title, c.heading_path, c.text)).lastrowid
                if self.fts_available:
                    self._db.execute("INSERT INTO chunk_fts (rowid, text) VALUES (?,?)", (cid, c.text))
            self._db.execute("INSERT OR IGNORE INTO chunk_snapshot VALUES (?,?,?)", (cid, snapshot_id, page_id))
            out.append(self.chunk(cid))
        self._db.commit()
        return out

    def _stored(self, r) -> StoredChunk:
        return StoredChunk(r["chunk_id"], Chunk(r["chunk_hash"], r["url"], r["title"], r["heading_path"], r["text"]))

    def new_chunks(self, snapshot_id: str) -> list[StoredChunk]:
        rows = self._db.execute(
            """SELECT c.* FROM chunk c WHERE c.chunk_id IN
                 (SELECT chunk_id FROM chunk_snapshot WHERE snapshot_id=?)
               AND NOT EXISTS (
                 SELECT 1 FROM chunk_snapshot cs JOIN snapshot s ON s.snapshot_id=cs.snapshot_id
                 WHERE cs.chunk_id=c.chunk_id
                   AND s.taken_on < (SELECT taken_on FROM snapshot WHERE snapshot_id=?))
               ORDER BY c.chunk_id""", (snapshot_id, snapshot_id)).fetchall()
        return [self._stored(r) for r in rows]

    def page_chunks(self, snapshot_id: str, url: str) -> list[Hit]:
        """All sections of one page in page order (score 0): context around a fact the page states."""
        rows = self._db.execute(
            "SELECT c.* FROM chunk c JOIN chunk_snapshot cs ON cs.chunk_id=c.chunk_id WHERE cs.snapshot_id=? AND c.url=? "
            "ORDER BY c.chunk_id", (snapshot_id, url)).fetchall()
        return [Hit(r["chunk_id"], r["url"], r["heading_path"], r["text"], 0.0, snapshot_id) for r in rows]

    def chunk(self, chunk_id: int) -> StoredChunk:
        r = self._db.execute("SELECT * FROM chunk WHERE chunk_id=?", (chunk_id,)).fetchone()
        if r is None:
            raise KeyError(chunk_id)
        return self._stored(r)

    def chunk_ids_in(self, snapshot_id: str) -> set[int]:
        return {r[0] for r in self._db.execute(
            "SELECT DISTINCT chunk_id FROM chunk_snapshot WHERE snapshot_id=?", (snapshot_id,))}

    def fts_match(self, match_expr: str, snapshot_id: str, k: int) -> list[tuple[int, float]]:
        if not self.fts_available:
            raise RuntimeError("SQLite FTS5 is not available")
        rows = self._db.execute(
            # EXISTS (not "rowid IN (subquery)"): the IN form makes SQLite probe FTS per row, ~25 s on 55k chunks.
            """SELECT f.rowid, -bm25(chunk_fts) AS score FROM chunk_fts f
               WHERE chunk_fts MATCH ?
                 AND EXISTS (SELECT 1 FROM chunk_snapshot cs WHERE cs.chunk_id=f.rowid AND cs.snapshot_id=?)
               ORDER BY score DESC, f.rowid LIMIT ?""", (match_expr, snapshot_id, k)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def live_urls(self, snapshot_id: str) -> set[str]:
        return {r[0] for r in self._db.execute("SELECT url FROM page WHERE snapshot_id=?", (snapshot_id,))}

    # facts
    def add_facts(self, obs: list[FactObs]) -> None:
        self._db.executemany(
            f"INSERT INTO fact_obs ({','.join(_OBS_COLS)},qualifiers_json) VALUES ({','.join('?' * (len(_OBS_COLS) + 1))})",
            [tuple(getattr(o, c) for c in _OBS_COLS) + (json.dumps(o.qualifiers, sort_keys=True),) for o in obs])
        self._db.commit()

    def facts(self, *, fact_key: str = None, product: str = None, attribute: str = None,
              snapshot_id: str = None) -> list[FactObs]:
        where, args = [], []
        for col, v in (("fact_key", fact_key), ("product", product), ("attribute", attribute), ("snapshot_id", snapshot_id)):
            if v is not None:
                where.append(f"{col}=?")
                args.append(v)
        sql = "SELECT * FROM fact_obs" + (" WHERE " + " AND ".join(where) if where else "") + \
              " ORDER BY fact_key, taken_on, url, obs_id"
        return [_obs_from_row(r) for r in self._db.execute(sql, args)]

    def fact_keys(self, snapshot_id: Optional[str] = None) -> list[str]:
        if snapshot_id is None:
            rows = self._db.execute("SELECT DISTINCT fact_key FROM fact_obs ORDER BY fact_key")
        else:
            rows = self._db.execute("SELECT DISTINCT fact_key FROM fact_obs WHERE snapshot_id=? ORDER BY fact_key",
                                    (snapshot_id,))
        return [r[0] for r in rows]

    def set_resolved(self, snapshot_id: str, resolved: list[ResolvedFact]) -> None:
        self._db.execute("DELETE FROM fact_resolved WHERE snapshot_id=?", (snapshot_id,))
        self._db.executemany(
            "INSERT INTO fact_resolved (fact_key,snapshot_id,value_norm,obs_id,losers_json,winner_json,rule) "
            "VALUES (?,?,?,NULL,?,?,?)",
            [(r.fact_key, snapshot_id, r.value, json.dumps([asdict(l) for l in r.losers]),
              json.dumps(asdict(r.winner)), r.rule) for r in resolved])
        self._db.commit()

    def resolved(self, snapshot_id: str, product: Optional[str] = None,
                 attribute: Optional[str] = None) -> list[ResolvedFact]:
        out = []
        for r in self._db.execute("SELECT * FROM fact_resolved WHERE snapshot_id=? ORDER BY fact_key", (snapshot_id,)):
            p, a, _ = parse_key(r["fact_key"])
            if (product is not None and p != product) or (attribute is not None and a != attribute):
                continue
            out.append(ResolvedFact(r["fact_key"], r["value_norm"], FactObs(**json.loads(r["winner_json"])),
                                    [FactObs(**d) for d in json.loads(r["losers_json"])], r["rule"]))
        return out

    # memo
    def memo_get(self, key: str, snapshot_id: str) -> Optional[dict]:
        r = self._db.execute("SELECT answer_json FROM answer_memo WHERE intent_key=? AND snapshot_id=?",
                             (key, snapshot_id)).fetchone()
        return json.loads(r[0]) if r else None

    def memo_put(self, key: str, snapshot_id: str, answer: dict) -> None:
        self._db.execute("INSERT OR REPLACE INTO answer_memo VALUES (?,?,?)", (key, snapshot_id, json.dumps(answer)))
        self._db.commit()

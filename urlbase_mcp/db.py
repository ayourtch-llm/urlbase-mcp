from __future__ import annotations

import json
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Iterable, Optional, Sequence

import sqlite_vec

_local = threading.local()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn(db_path: Path) -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "db_path", None) != db_path:
        conn = _connect(db_path)
        _local.conn = conn
        _local.db_path = db_path
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    content_hash TEXT,
    etag TEXT,
    last_modified TEXT,
    fetched_at TEXT,
    added_at TEXT DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    extract_mode TEXT NOT NULL DEFAULT 'article',
    byte_size INTEGER,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    content_type TEXT
);

CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);
CREATE INDEX IF NOT EXISTS idx_sources_fetched ON sources(fetched_at);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE(source_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
"""


def init_db(db_path: Path, embed_dim: int) -> None:
    conn = get_conn(db_path)
    conn.executescript(SCHEMA_SQL)

    row = conn.execute("SELECT value FROM meta WHERE key='embed_dim'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('embed_dim', ?)", (str(embed_dim),)
        )
    else:
        existing = int(row["value"])
        if existing != embed_dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: DB was built with {existing}-dim "
                f"vectors, but configured model produces {embed_dim}-dim. "
                f"Either change URLBASE_EMBED_MODEL back, or delete {db_path} to start fresh."
            )

    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
        f"chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{embed_dim}])"
    )


def serialize_vec(vec: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def upsert_source(
    db_path: Path,
    url: str,
    tags: list[str],
    extract_mode: str,
) -> int:
    conn = get_conn(db_path)
    cur = conn.execute(
        "INSERT INTO sources(url, tags, extract_mode) VALUES(?, ?, ?) "
        "ON CONFLICT(url) DO UPDATE SET tags=excluded.tags, extract_mode=excluded.extract_mode "
        "RETURNING id",
        (url, json.dumps(tags), extract_mode),
    )
    return int(cur.fetchone()["id"])


def get_source_by_url(db_path: Path, url: str) -> Optional[sqlite3.Row]:
    conn = get_conn(db_path)
    return conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()


def get_source(db_path: Path, source_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn(db_path)
    return conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()


def resolve_source(db_path: Path, ref: str | int) -> Optional[sqlite3.Row]:
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        return get_source(db_path, int(ref))
    return get_source_by_url(db_path, ref)


def list_sources(
    db_path: Path, tag: Optional[str] = None, status: Optional[str] = None
) -> list[sqlite3.Row]:
    conn = get_conn(db_path)
    sql = "SELECT * FROM sources WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"
        params.append(status)
    rows = conn.execute(sql + " ORDER BY added_at DESC", params).fetchall()
    if tag is None:
        return rows
    out = []
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except Exception:
            tags = []
        if tag in tags:
            out.append(r)
    return out


def delete_source(db_path: Path, source_id: int) -> int:
    conn = get_conn(db_path)
    conn.execute("BEGIN")
    try:
        conn.execute(
            "DELETE FROM vec_chunks WHERE chunk_id IN "
            "(SELECT id FROM chunks WHERE source_id=?)",
            (source_id,),
        )
        cur = conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
        conn.execute("COMMIT")
        return cur.rowcount
    except Exception:
        conn.execute("ROLLBACK")
        raise


def replace_chunks(
    db_path: Path,
    source_id: int,
    chunks: Sequence[str],
    embeddings: Sequence[Sequence[float]],
) -> None:
    assert len(chunks) == len(embeddings)
    conn = get_conn(db_path)
    conn.execute("BEGIN")
    try:
        conn.execute(
            "DELETE FROM vec_chunks WHERE chunk_id IN "
            "(SELECT id FROM chunks WHERE source_id=?)",
            (source_id,),
        )
        conn.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
        for i, (text, vec) in enumerate(zip(chunks, embeddings)):
            cur = conn.execute(
                "INSERT INTO chunks(source_id, idx, text) VALUES(?, ?, ?)",
                (source_id, i, text),
            )
            chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO vec_chunks(chunk_id, embedding) VALUES(?, ?)",
                (chunk_id, serialize_vec(vec)),
            )
        conn.execute(
            "UPDATE sources SET chunk_count=? WHERE id=?",
            (len(chunks), source_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def update_source_meta(
    db_path: Path,
    source_id: int,
    *,
    title: Optional[str] = None,
    content_hash: Optional[str] = None,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    status: Optional[str] = None,
    error: Optional[str] = None,
    byte_size: Optional[int] = None,
    content_type: Optional[str] = None,
    set_fetched_now: bool = False,
) -> None:
    conn = get_conn(db_path)
    sets = []
    params: list = []
    if title is not None:
        sets.append("title=?")
        params.append(title)
    if content_hash is not None:
        sets.append("content_hash=?")
        params.append(content_hash)
    if etag is not None:
        sets.append("etag=?")
        params.append(etag)
    if last_modified is not None:
        sets.append("last_modified=?")
        params.append(last_modified)
    if status is not None:
        sets.append("status=?")
        params.append(status)
    if error is not None or status == "ok":
        sets.append("error=?")
        params.append(None if status == "ok" else error)
    if byte_size is not None:
        sets.append("byte_size=?")
        params.append(byte_size)
    if content_type is not None:
        sets.append("content_type=?")
        params.append(content_type)
    if set_fetched_now:
        sets.append("fetched_at=datetime('now')")
    if not sets:
        return
    params.append(source_id)
    conn.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id=?", params)


def search_vec(
    db_path: Path, query_vec: Sequence[float], k: int
) -> list[tuple[int, float]]:
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT chunk_id, distance FROM vec_chunks "
        "WHERE embedding MATCH ? AND k=? ORDER BY distance",
        (serialize_vec(query_vec), k),
    ).fetchall()
    return [(int(r["chunk_id"]), float(r["distance"])) for r in rows]


def fetch_chunks(
    db_path: Path, chunk_ids: Iterable[int]
) -> dict[int, sqlite3.Row]:
    conn = get_conn(db_path)
    ids = list(chunk_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT c.id, c.source_id, c.idx, c.text, s.url, s.title "
        f"FROM chunks c JOIN sources s ON s.id = c.source_id "
        f"WHERE c.id IN ({placeholders})",
        ids,
    ).fetchall()
    return {int(r["id"]): r for r in rows}


def neighbor_chunks(
    db_path: Path, source_id: int, idx: int, window: int
) -> list[sqlite3.Row]:
    conn = get_conn(db_path)
    return conn.execute(
        "SELECT id, idx, text FROM chunks WHERE source_id=? AND idx BETWEEN ? AND ? "
        "ORDER BY idx",
        (source_id, idx - window, idx + window),
    ).fetchall()


def all_chunks_for_source(db_path: Path, source_id: int) -> list[sqlite3.Row]:
    conn = get_conn(db_path)
    return conn.execute(
        "SELECT id, idx, text FROM chunks WHERE source_id=? ORDER BY idx",
        (source_id,),
    ).fetchall()


def stats(db_path: Path) -> dict:
    conn = get_conn(db_path)
    src_count = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
    chunk_count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    by_status_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM sources GROUP BY status"
    ).fetchall()
    by_status = {r["status"]: r["n"] for r in by_status_rows}
    last_fetch = conn.execute(
        "SELECT MAX(fetched_at) AS t FROM sources"
    ).fetchone()["t"]
    return {
        "sources": src_count,
        "chunks": chunk_count,
        "by_status": by_status,
        "last_fetch": last_fetch,
    }

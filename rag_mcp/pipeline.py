from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import db, fetch
from .chunk import chunk_text
from .config import Config
from .embed import Embedder


@dataclass
class IngestResult:
    source_id: int
    url: str
    status: str  # "ok", "unchanged", "error"
    title: Optional[str]
    chunks: int
    error: Optional[str] = None


def add_or_refresh(
    cfg: Config,
    embedder: Embedder,
    url: str,
    *,
    tags: Optional[List[str]] = None,
    extract_mode: Optional[str] = None,
    force: bool = False,
) -> IngestResult:
    tags = tags or []
    mode = (extract_mode or cfg.extract_mode_default).lower()
    if mode not in ("article", "full"):
        mode = "article"

    existing = db.get_source_by_url(cfg.db_path, url)
    if existing is None:
        source_id = db.upsert_source(cfg.db_path, url, tags, mode)
        etag = None
        last_modified = None
        prev_hash = None
    else:
        source_id = int(existing["id"])
        # Update tags / extract_mode if caller supplied new ones.
        db.upsert_source(cfg.db_path, url, tags or _parse_tags(existing["tags"]), mode)
        etag = None if force else existing["etag"]
        last_modified = None if force else existing["last_modified"]
        prev_hash = existing["content_hash"]

    result = fetch.fetch_url(
        url,
        extract_mode=mode,
        etag=etag,
        last_modified=last_modified,
        user_agent=cfg.user_agent,
        timeout=cfg.fetch_timeout,
        max_bytes=cfg.max_bytes,
    )

    if result.status == "unchanged":
        db.update_source_meta(
            cfg.db_path,
            source_id,
            status="ok",
            set_fetched_now=True,
        )
        return IngestResult(
            source_id=source_id,
            url=url,
            status="unchanged",
            title=existing["title"] if existing else None,
            chunks=int(existing["chunk_count"]) if existing else 0,
        )

    if result.status == "error":
        db.update_source_meta(
            cfg.db_path,
            source_id,
            status="error",
            error=result.error,
            set_fetched_now=True,
            content_type=result.content_type,
        )
        return IngestResult(
            source_id=source_id,
            url=url,
            status="error",
            title=result.title,
            chunks=0,
            error=result.error,
        )

    if not force and prev_hash and prev_hash == result.content_hash:
        db.update_source_meta(
            cfg.db_path,
            source_id,
            status="ok",
            set_fetched_now=True,
            etag=result.etag or "",
            last_modified=result.last_modified or "",
            byte_size=result.byte_size,
            content_type=result.content_type,
        )
        return IngestResult(
            source_id=source_id,
            url=url,
            status="unchanged",
            title=result.title or (existing["title"] if existing else None),
            chunks=int(existing["chunk_count"]) if existing else 0,
        )

    pieces = chunk_text(result.text or "", cfg.chunk_chars, cfg.chunk_overlap)
    if not pieces:
        db.update_source_meta(
            cfg.db_path,
            source_id,
            status="error",
            error="no chunks produced",
            set_fetched_now=True,
        )
        return IngestResult(
            source_id=source_id,
            url=url,
            status="error",
            title=result.title,
            chunks=0,
            error="no chunks produced",
        )

    vectors = embedder.embed_passages(pieces)
    db.replace_chunks(cfg.db_path, source_id, pieces, vectors)
    db.update_source_meta(
        cfg.db_path,
        source_id,
        title=result.title or url,
        content_hash=result.content_hash,
        etag=result.etag or "",
        last_modified=result.last_modified or "",
        status="ok",
        error="",
        byte_size=result.byte_size,
        content_type=result.content_type,
        set_fetched_now=True,
    )

    return IngestResult(
        source_id=source_id,
        url=url,
        status="ok",
        title=result.title or url,
        chunks=len(pieces),
    )


def _parse_tags(raw: Optional[str]) -> List[str]:
    import json

    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [str(x) for x in v]
    except Exception:
        pass
    return []


@dataclass
class SearchHit:
    chunk_id: int
    source_id: int
    url: str
    title: Optional[str]
    idx: int
    text: str
    vector_distance: float
    rerank_score: Optional[float]


def search(
    cfg: Config,
    embedder: Embedder,
    query: str,
    *,
    k: int = 5,
    tag_filter: Optional[str] = None,
    url_filter: Optional[str] = None,
    candidate_k: Optional[int] = None,
) -> List[SearchHit]:
    if not query.strip():
        return []
    cand_k = candidate_k or max(k * 4, 20)
    qvec = embedder.embed_query(query)
    raw = db.search_vec(cfg.db_path, qvec, cand_k)
    if not raw:
        return []

    chunks = db.fetch_chunks(cfg.db_path, [cid for cid, _ in raw])
    distances = {cid: dist for cid, dist in raw}

    # Optional filtering by URL substring and/or tag.
    filtered: list = []
    if tag_filter is not None:
        import json
        tag_source_ids = {
            int(r["id"]) for r in db.list_sources(cfg.db_path, tag=tag_filter)
        }
    else:
        tag_source_ids = None

    for cid, row in chunks.items():
        if tag_source_ids is not None and int(row["source_id"]) not in tag_source_ids:
            continue
        if url_filter and url_filter not in row["url"]:
            continue
        filtered.append((cid, row))

    if not filtered:
        return []

    if cfg.rerank_enabled and len(filtered) > 1:
        docs = [row["text"] for _, row in filtered]
        scores = embedder.rerank(query, docs)
        scored = list(zip(filtered, scores))
        scored.sort(key=lambda x: x[1], reverse=True)
        filtered = [item for item, _ in scored]
        score_map = {item[0]: score for item, score in scored}
    else:
        filtered.sort(key=lambda x: distances[x[0]])
        score_map = {cid: None for cid, _ in filtered}

    hits: List[SearchHit] = []
    for cid, row in filtered[:k]:
        hits.append(
            SearchHit(
                chunk_id=cid,
                source_id=int(row["source_id"]),
                url=row["url"],
                title=row["title"],
                idx=int(row["idx"]),
                text=row["text"],
                vector_distance=distances[cid],
                rerank_score=score_map.get(cid),
            )
        )
    return hits

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from . import db, pipeline
from .config import Config
from .embed import Embedder
from .refresh import RefreshThread

logger = logging.getLogger("rag_mcp")


def _setup_logging() -> None:
    level = os.environ.get("RAG_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )


def _row_to_source_dict(row) -> dict:
    try:
        tags = json.loads(row["tags"] or "[]")
    except Exception:
        tags = []
    return {
        "id": row["id"],
        "url": row["url"],
        "title": row["title"],
        "status": row["status"],
        "tags": tags,
        "extract_mode": row["extract_mode"],
        "fetched_at": row["fetched_at"],
        "added_at": row["added_at"],
        "chunk_count": row["chunk_count"],
        "byte_size": row["byte_size"],
        "content_type": row["content_type"],
        "error": row["error"],
    }


def build_server(cfg: Config) -> FastMCP:
    embedder = Embedder(cfg.embed_model, cfg.reranker_model, cfg.rerank_enabled)
    # Force model load up-front so dimension is known and DB matches.
    dim = embedder.dimension
    db.init_db(cfg.db_path, dim)
    logger.info(
        "rag-mcp ready: db=%s embed_model=%s dim=%d rerank=%s",
        cfg.db_path,
        cfg.embed_model,
        dim,
        cfg.rerank_enabled,
    )

    refresher = RefreshThread(cfg, embedder)
    refresher.start()

    mcp = FastMCP("rag-mcp")

    @mcp.tool()
    async def add_url(
        url: str,
        tags: Optional[List[str]] = None,
        extract_mode: Optional[str] = None,
        force: bool = False,
    ) -> dict:
        """Fetch a URL, extract and chunk its content, embed, and store it.

        - url: HTTP(S) URL to ingest.
        - tags: optional list of tag strings for later filtering.
        - extract_mode: 'article' (default; main content only, best for HTML pages)
          or 'full' (everything trafilatura can pull). Ignored for PDFs / plain text.
        - force: re-fetch and re-embed even if ETag/hash say nothing changed.
        """
        res = await asyncio.to_thread(
            pipeline.add_or_refresh,
            cfg,
            embedder,
            url,
            tags=tags,
            extract_mode=extract_mode,
            force=force,
        )
        return {
            "id": res.source_id,
            "url": res.url,
            "status": res.status,
            "title": res.title,
            "chunks": res.chunks,
            "error": res.error,
        }

    @mcp.tool()
    async def remove_url(url_or_id: str) -> dict:
        """Remove a source (by URL or numeric id) and all its chunks/vectors."""
        def _do() -> dict:
            row = db.resolve_source(cfg.db_path, url_or_id)
            if row is None:
                return {"removed": 0, "error": "not found"}
            deleted = db.delete_source(cfg.db_path, int(row["id"]))
            return {"removed": deleted, "url": row["url"], "id": int(row["id"])}

        return await asyncio.to_thread(_do)

    @mcp.tool()
    async def list_urls(
        tag: Optional[str] = None, status: Optional[str] = None
    ) -> dict:
        """List indexed sources, optionally filtered by tag or status (ok/error/pending)."""
        def _do() -> dict:
            rows = db.list_sources(cfg.db_path, tag=tag, status=status)
            return {"count": len(rows), "sources": [_row_to_source_dict(r) for r in rows]}

        return await asyncio.to_thread(_do)

    @mcp.tool()
    async def refresh(target: str = "all") -> dict:
        """Re-fetch source(s). target='all' refreshes every stored URL; else pass a URL or id."""
        def _do() -> dict:
            if target == "all":
                rows = db.list_sources(cfg.db_path)
                results = []
                for r in rows:
                    res = pipeline.add_or_refresh(cfg, embedder, r["url"])
                    results.append(
                        {
                            "url": res.url,
                            "status": res.status,
                            "chunks": res.chunks,
                            "error": res.error,
                        }
                    )
                return {"refreshed": len(results), "results": results}
            row = db.resolve_source(cfg.db_path, target)
            if row is None:
                return {"error": "not found"}
            res = pipeline.add_or_refresh(cfg, embedder, row["url"], force=True)
            return {
                "url": res.url,
                "status": res.status,
                "chunks": res.chunks,
                "error": res.error,
            }

        return await asyncio.to_thread(_do)

    @mcp.tool()
    async def search(
        query: str,
        k: int = 5,
        tag_filter: Optional[str] = None,
        url_filter: Optional[str] = None,
    ) -> dict:
        """RAG search across all stored documents.

        - query: natural-language query.
        - k: number of chunks to return (default 5).
        - tag_filter: only return chunks from sources with this tag.
        - url_filter: only return chunks whose source URL contains this substring.
        """
        hits = await asyncio.to_thread(
            pipeline.search,
            cfg,
            embedder,
            query,
            k=k,
            tag_filter=tag_filter,
            url_filter=url_filter,
        )
        return {
            "query": query,
            "count": len(hits),
            "hits": [
                {
                    "chunk_id": h.chunk_id,
                    "source_id": h.source_id,
                    "url": h.url,
                    "title": h.title,
                    "idx": h.idx,
                    "text": h.text,
                    "vector_distance": h.vector_distance,
                    "rerank_score": h.rerank_score,
                }
                for h in hits
            ],
        }

    @mcp.tool()
    async def get_document(url_or_id: str) -> dict:
        """Return the full reconstructed text of a stored source."""
        def _do() -> dict:
            row = db.resolve_source(cfg.db_path, url_or_id)
            if row is None:
                return {"error": "not found"}
            chunks = db.all_chunks_for_source(cfg.db_path, int(row["id"]))
            text = "\n\n".join(c["text"] for c in chunks)
            return {
                "id": int(row["id"]),
                "url": row["url"],
                "title": row["title"],
                "chunk_count": len(chunks),
                "text": text,
            }

        return await asyncio.to_thread(_do)

    @mcp.tool()
    async def get_chunk(chunk_id: int, context_window: int = 1) -> dict:
        """Return a chunk plus N neighbors on each side for surrounding context."""
        def _do() -> dict:
            base = db.fetch_chunks(cfg.db_path, [chunk_id]).get(chunk_id)
            if base is None:
                return {"error": "not found"}
            neighbors = db.neighbor_chunks(
                cfg.db_path,
                int(base["source_id"]),
                int(base["idx"]),
                max(0, context_window),
            )
            return {
                "chunk_id": chunk_id,
                "url": base["url"],
                "title": base["title"],
                "idx": int(base["idx"]),
                "neighbors": [
                    {"chunk_id": int(n["id"]), "idx": int(n["idx"]), "text": n["text"]}
                    for n in neighbors
                ],
            }

        return await asyncio.to_thread(_do)

    @mcp.tool()
    async def stats() -> dict:
        """Return counts and last-fetch timestamps for the index."""
        return await asyncio.to_thread(db.stats, cfg.db_path)

    return mcp


def main() -> None:
    _setup_logging()
    cfg = Config.from_env()
    server = build_server(cfg)
    server.run()


if __name__ == "__main__":
    main()

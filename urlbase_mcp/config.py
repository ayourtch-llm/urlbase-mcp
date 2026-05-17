from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return int(v)


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v


def _default_db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".local" / "share"
    return root / "urlbase-mcp" / "urlbase.db"


@dataclass(frozen=True)
class Config:
    db_path: Path
    embed_model: str
    reranker_model: str
    refresh_interval_hours: int
    refresh_jitter_min: int
    chunk_chars: int
    chunk_overlap: int
    fetch_timeout: int
    max_bytes: int
    extract_mode_default: str  # "article" or "full"
    user_agent: str
    rerank_enabled: bool

    @classmethod
    def from_env(cls) -> "Config":
        db_path_str = os.environ.get("URLBASE_DB_PATH")
        db_path = Path(db_path_str) if db_path_str else _default_db_path()
        extract_mode = _env_str("URLBASE_EXTRACT_MODE", "article").lower()
        if extract_mode not in ("article", "full"):
            extract_mode = "article"
        rerank = _env_str("URLBASE_RERANK", "1").lower() not in ("0", "false", "no")
        return cls(
            db_path=db_path,
            embed_model=_env_str("URLBASE_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
            reranker_model=_env_str(
                "URLBASE_RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2"
            ),
            refresh_interval_hours=_env_int("URLBASE_REFRESH_INTERVAL_HOURS", 24),
            refresh_jitter_min=_env_int("URLBASE_REFRESH_JITTER_MIN", 30),
            chunk_chars=_env_int("URLBASE_CHUNK_CHARS", 2400),
            chunk_overlap=_env_int("URLBASE_CHUNK_OVERLAP", 320),
            fetch_timeout=_env_int("URLBASE_FETCH_TIMEOUT", 30),
            max_bytes=_env_int("URLBASE_MAX_BYTES", 20_000_000),
            extract_mode_default=extract_mode,
            user_agent=_env_str(
                "URLBASE_USER_AGENT", "urlbase-mcp/0.1 (+https://github.com/)"
            ),
            rerank_enabled=rerank,
        )

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class FetchResult:
    status: str  # "ok", "unchanged", "error"
    title: Optional[str]
    text: Optional[str]
    content_hash: Optional[str]
    etag: Optional[str]
    last_modified: Optional[str]
    byte_size: Optional[int]
    content_type: Optional[str]
    error: Optional[str] = None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_html(body: bytes, mode: str) -> tuple[Optional[str], str]:
    """Returns (title, text)."""
    import trafilatura

    try:
        html_str = body.decode("utf-8", errors="replace")
    except Exception:
        html_str = body.decode("latin-1", errors="replace")

    if mode == "article":
        text = trafilatura.extract(
            html_str,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        if not text:
            # fall back to a stripped-tags version so we at least get something
            text = trafilatura.extract(
                html_str, no_fallback=False, favor_recall=True
            ) or ""
    else:
        # "full": grab everything trafilatura can pull (incl. recall mode), then concatenate
        text = trafilatura.extract(
            html_str,
            include_comments=True,
            include_tables=True,
            favor_recall=True,
            no_fallback=False,
        ) or ""

    title = None
    try:
        meta = trafilatura.extract_metadata(html_str)
        if meta:
            title = meta.title
    except Exception:
        pass

    return title, text or ""


def _extract_pdf(body: bytes) -> tuple[Optional[str], str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n\n".join(parts).strip()
    title = None
    try:
        meta = reader.metadata
        if meta and meta.title:
            title = str(meta.title)
    except Exception:
        pass
    return title, text


def _extract_text(body: bytes) -> tuple[Optional[str], str]:
    try:
        return None, body.decode("utf-8")
    except UnicodeDecodeError:
        return None, body.decode("utf-8", errors="replace")


def fetch_url(
    url: str,
    *,
    extract_mode: str = "article",
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    user_agent: str = "rag-mcp/0.1",
    timeout: int = 30,
    max_bytes: int = 20_000_000,
) -> FetchResult:
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        with httpx.Client(
            follow_redirects=True, timeout=timeout, headers=headers
        ) as client:
            resp = client.get(url)
    except Exception as e:
        return FetchResult(
            status="error",
            title=None,
            text=None,
            content_hash=None,
            etag=None,
            last_modified=None,
            byte_size=None,
            content_type=None,
            error=f"fetch failed: {e}",
        )

    if resp.status_code == 304:
        return FetchResult(
            status="unchanged",
            title=None,
            text=None,
            content_hash=None,
            etag=etag,
            last_modified=last_modified,
            byte_size=None,
            content_type=None,
        )

    if resp.status_code >= 400:
        return FetchResult(
            status="error",
            title=None,
            text=None,
            content_hash=None,
            etag=None,
            last_modified=None,
            byte_size=None,
            content_type=None,
            error=f"HTTP {resp.status_code}",
        )

    body = resp.content
    if len(body) > max_bytes:
        return FetchResult(
            status="error",
            title=None,
            text=None,
            content_hash=None,
            etag=None,
            last_modified=None,
            byte_size=len(body),
            content_type=resp.headers.get("content-type"),
            error=f"response too large: {len(body)} > {max_bytes}",
        )

    content_type = (resp.headers.get("content-type") or "").lower()
    url_lower = url.lower()

    if "application/pdf" in content_type or url_lower.endswith(".pdf"):
        kind = "pdf"
    elif "text/html" in content_type or "application/xhtml" in content_type or (
        not content_type and ("<html" in body[:2048].lower() or b"<!doctype" in body[:512].lower())
    ):
        kind = "html"
    else:
        kind = "text"

    try:
        if kind == "html":
            title, text = _extract_html(body, extract_mode)
        elif kind == "pdf":
            title, text = _extract_pdf(body)
        else:
            title, text = _extract_text(body)
    except Exception as e:
        return FetchResult(
            status="error",
            title=None,
            text=None,
            content_hash=None,
            etag=None,
            last_modified=None,
            byte_size=len(body),
            content_type=content_type,
            error=f"extract failed: {e}",
        )

    text = (text or "").strip()
    if not text:
        return FetchResult(
            status="error",
            title=title,
            text=None,
            content_hash=None,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            byte_size=len(body),
            content_type=content_type,
            error="no text extracted",
        )

    return FetchResult(
        status="ok",
        title=title,
        text=text,
        content_hash=_hash(text),
        etag=resp.headers.get("etag"),
        last_modified=resp.headers.get("last-modified"),
        byte_size=len(body),
        content_type=content_type,
    )

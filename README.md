# urlbase-mcp

An MCP server that fetches HTTP(S) documents, chunks and embeds them locally,
and exposes RAG search as MCP tools.

> **New here?** Start with the [User Guide](GUIDE.md) for a friendly
> walkthrough. This README is the technical reference.

- Local embeddings via [`fastembed`](https://github.com/qdrant/fastembed) (ONNX, no PyTorch)
- Storage in a single SQLite file (with [`sqlite-vec`](https://github.com/asg017/sqlite-vec) for vector search)
- HTML article extraction via `trafilatura`, PDF via `pypdf`
- Optional cross-encoder reranking
- Background daily refresh thread (ETag / Last-Modified aware)

## Install

Requires Python 3.10+.

```bash
# from a clone:
pip install .

# or with uv / uvx (recommended once published):
uvx urlbase-mcp
```

## Run

```bash
urlbase-mcp
```

It speaks MCP over stdio. On first start it will download the embedding model
(~130 MB) into `~/.cache/`.

## MCP client configuration

Claude Desktop / other MCP clients:

```json
{
  "mcpServers": {
    "urlbase": {
      "command": "urlbase-mcp"
    }
  }
}
```

## Environment variables

| Var | Default | Notes |
| --- | --- | --- |
| `URLBASE_DB_PATH` | `~/.local/share/urlbase-mcp/urlbase.db` | SQLite file path |
| `URLBASE_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Any fastembed-supported model |
| `URLBASE_RERANKER_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | Cross-encoder reranker |
| `URLBASE_RERANK` | `1` | `0`/`false` to disable rerank |
| `URLBASE_REFRESH_INTERVAL_HOURS` | `24` | `0` disables background refresh |
| `URLBASE_REFRESH_JITTER_MIN` | `30` | Random extra minutes per cycle |
| `URLBASE_CHUNK_CHARS` | `2400` | Target chunk size in characters |
| `URLBASE_CHUNK_OVERLAP` | `320` | Char overlap between chunks |
| `URLBASE_FETCH_TIMEOUT` | `30` | HTTP timeout (seconds) |
| `URLBASE_MAX_BYTES` | `20000000` | Reject responses larger than this |
| `URLBASE_EXTRACT_MODE` | `article` | Default HTML extraction mode: `article` or `full` |
| `URLBASE_USER_AGENT` | `urlbase-mcp/0.1` | Sent to remote servers |
| `URLBASE_LOG_LEVEL` | `INFO` | Standard Python levels |

## Tools

- `add_url(url, tags?, extract_mode?, force?)` — fetch, chunk, embed, store. Skips re-embedding via ETag/Last-Modified/content hash unless `force=true`. Per-URL `extract_mode` overrides the default.
- `remove_url(url_or_id)` — delete a source and its chunks.
- `list_urls(tag?, status?)` — list sources with metadata.
- `refresh(target?)` — `target="all"` re-fetches everything; otherwise pass a URL or id (forced).
- `search(query, k?, tag_filter?, url_filter?)` — vector search + optional rerank.
- `get_document(url_or_id)` — return full reconstructed text.
- `get_chunk(chunk_id, context_window?)` — chunk plus N neighbors.
- `stats()` — counts and last-fetch timestamp.

## Notes

- Changing `URLBASE_EMBED_MODEL` after some data is indexed is an error if the
  dimension differs. Delete the DB to start fresh.
- The refresh thread also recomputes embeddings on content changes, so swapping
  a remote document for a new version is picked up automatically.
- Public URLs only — no auth header support is wired in.

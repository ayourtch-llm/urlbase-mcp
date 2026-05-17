# urlbase-mcp User Guide

A friendly walkthrough of what `urlbase-mcp` does, how to set it up, and how to
actually *use* it day-to-day from Claude or another MCP client.

> **Quick mental model:** you give it a list of web pages and PDFs you care
> about. It downloads them, splits them into small passages, and indexes those
> passages by meaning (not just keywords). Then when you ask a question, it
> finds the passages that best answer it and hands them to your AI assistant.
> Nothing leaves your machine — embeddings run locally.

---

## Table of contents

1. [What is this for?](#what-is-this-for)
2. [Installation](#installation)
3. [Hooking it up to Claude Desktop](#hooking-it-up-to-claude-desktop)
4. [Your first session](#your-first-session)
5. [Everyday workflows](#everyday-workflows)
6. [Tips for good results](#tips-for-good-results)
7. [Configuration cheat sheet](#configuration-cheat-sheet)
8. [Troubleshooting](#troubleshooting)
9. [FAQ](#faq)

---

## What is this for?

You probably have a collection of pages you'd love your AI assistant to "just
know about": a few RFCs, a vendor's API docs, a long-form blog series you keep
re-reading, the PDFs of papers you've been meaning to understand.

`urlbase-mcp` lets you point at those URLs once and then ask questions like:

- *"What does the authentication section of the Stripe API doc say about webhook signatures?"*
- *"Compare what the FastAPI and Flask docs say about background tasks."*
- *"In that paper I added last week, what was the reported speedup vs the baseline?"*

The assistant searches your private index and answers from the matched
passages — citing which URL each passage came from.

### What it is not

- **Not a web browser.** It only knows about URLs you've explicitly added.
- **Not a crawler.** It doesn't follow links — each URL is one document.
- **Not for private/auth'd pages.** Public URLs only.
- **Not magic.** Quality depends on the docs and on how you phrase queries.

---

## Installation

You need Python 3.10 or newer. Install [`uv`](https://github.com/astral-sh/uv)
first if you don't have it — it's the easiest way.

```bash
git clone <this repo> urlbase-mcp
cd urlbase-mcp
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

That gives you a `urlbase-mcp` command inside `.venv/bin/`.

### First run

```bash
.venv/bin/urlbase-mcp
```

On the very first run it will download two small models (~220 MB total) into
`~/.cache/`:

- `BAAI/bge-small-en-v1.5` — the embedding model
- `Xenova/ms-marco-MiniLM-L-6-v2` — the reranker

This is a one-time cost. Subsequent starts are instant.

The server speaks MCP over stdin/stdout, so running it interactively in a
terminal looks like it's hung — that's normal. Press Ctrl-C to stop.

---

## Hooking it up to Claude Desktop

Edit your Claude Desktop config:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add an `mcpServers` entry pointing at the `urlbase-mcp` binary:

```json
{
  "mcpServers": {
    "urlbase": {
      "command": "/Users/YOU/urlbase-mcp/.venv/bin/urlbase-mcp"
    }
  }
}
```

Restart Claude Desktop. You should see a tools indicator in the input box;
clicking it should list `add_url`, `search`, etc.

If you want to override defaults (database location, refresh interval, etc.)
add an `env` block:

```json
{
  "mcpServers": {
    "urlbase": {
      "command": "/Users/YOU/urlbase-mcp/.venv/bin/urlbase-mcp",
      "env": {
        "URLBASE_DB_PATH": "/Users/YOU/Documents/urlbase.db",
        "URLBASE_REFRESH_INTERVAL_HOURS": "12"
      }
    }
  }
}
```

---

## Your first session

Open a new Claude chat and try something like:

> *"Add https://en.wikipedia.org/wiki/Retrieval-augmented_generation to my knowledge base."*

Claude will call the `add_url` tool. You'll see a result like
`status=ok, chunks=9`.

Then:

> *"What is RAG and what are its main limitations?"*

Claude should call `search`, get back a few matching passages, and answer
using them — citing the URL.

That's the whole loop: **add → ask**.

---

## Everyday workflows

### Build a small knowledge base on a topic

> *"Add these three pages to my knowledge base and tag them 'kafka':*
> - *https://kafka.apache.org/documentation/#producerconfigs*
> - *https://kafka.apache.org/documentation/#consumerconfigs*
> - *https://kafka.apache.org/documentation/#brokerconfigs"*

Then later:

> *"Using only the kafka-tagged sources, what does `linger.ms` control and what's a sensible value?"*

The phrase "using only the kafka-tagged sources" hints Claude to pass
`tag_filter="kafka"` to `search`.

### Index a paper for later questions

> *"Add https://arxiv.org/pdf/2106.09685 with the tag 'lora-paper'."*

PDFs work the same as web pages. Then:

> *"In the lora-paper, what rank do the authors recommend for GPT-3 175B?"*

### Compare what two sources say

> *"Search my knowledge base for 'rate limiting' across all sources and tell
> me where each one differs."*

### See what's indexed

> *"List my indexed URLs."* — calls `list_urls`.
> *"How big is my knowledge base?"* — calls `stats`.

### Update or remove

> *"Refresh https://docs.example.com/api — they updated it yesterday."*
> *"Remove that blog post about Postgres locks, it's outdated."*

You don't need to remember tool names. Plain English works.

---

## Tips for good results

### Choose URLs carefully
A single well-written page usually beats five mediocre ones. The index will
happily store junk if you give it junk.

### Use tags
Tagging by topic (`kafka`, `terraform`, `phd-thesis-refs`) lets you scope
queries and avoids cross-talk between unrelated subjects.

### Pick the right extract mode
- **`article` (default):** strips nav, footers, sidebars, and ads — great for
  blog posts, news, Wikipedia-style content.
- **`full`:** keeps more of the page. Use this when `article` mode is dropping
  content you care about (sometimes happens on docs sites with unusual layouts,
  comment threads, or reference tables).

Set globally via `URLBASE_EXTRACT_MODE`, or per-URL when you call `add_url`:

> *"Add https://weird-docs.example.com using full extract mode."*

### Re-fetch when sources change
Daily refresh runs automatically. If you know a page just changed and don't
want to wait, ask Claude to *"refresh https://… now"*.

### Phrase queries as questions, not keywords
The search uses meaning, not term-matching. Full questions tend to work
better than three-word queries. "How does HNSW differ from IVF?" beats "HNSW
IVF".

### Ask for citations
By default Claude will usually mention the source URL, but you can be
explicit: *"Answer and quote the exact passage with its URL."*

---

## Configuration cheat sheet

All settings are environment variables. Defaults are good — only change what
you need.

| Variable | Default | What it does |
| --- | --- | --- |
| `URLBASE_DB_PATH` | `~/.local/share/urlbase-mcp/urlbase.db` | Where the SQLite file lives. Move this to back up or share. |
| `URLBASE_EXTRACT_MODE` | `article` | Default HTML extraction: `article` (clean) or `full` (more). |
| `URLBASE_REFRESH_INTERVAL_HOURS` | `24` | Background refresh period. `0` disables it. |
| `URLBASE_REFRESH_JITTER_MIN` | `30` | Random extra minutes per cycle so servers don't get a thundering herd. |
| `URLBASE_CHUNK_CHARS` | `2400` | Target chunk size. Bigger = more context per hit, fewer total hits. |
| `URLBASE_CHUNK_OVERLAP` | `320` | Char overlap between adjacent chunks. |
| `URLBASE_FETCH_TIMEOUT` | `30` | Seconds before giving up on a slow server. |
| `URLBASE_MAX_BYTES` | `20000000` | Reject documents larger than ~20 MB. |
| `URLBASE_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Change at your peril (see below). |
| `URLBASE_RERANKER_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | Cross-encoder used to re-score top hits. |
| `URLBASE_RERANK` | `1` | Set to `0` to skip reranking (faster, slightly worse). |
| `URLBASE_USER_AGENT` | `urlbase-mcp/0.1 …` | Sent to remote servers. Customise for politeness. |
| `URLBASE_LOG_LEVEL` | `INFO` | `DEBUG` if you're hunting a problem. |

### Changing the embedding model
The dimension is baked into the database when you first index something.
If you change `URLBASE_EMBED_MODEL` to a model with a different dimension, the
server will refuse to start and tell you so. Fix: either change it back, or
delete the DB file and re-ingest.

---

## Troubleshooting

### "First start is slow"
The model download. ~220 MB on the first run, then never again unless you
clear `~/.cache/`.

### "Search returns nothing useful"
- Did you actually add the relevant URL? *"List my URLs."*
- Did the fetch succeed? *"List my URLs filtered by status error."*
- Try the query as a full sentence question, not keywords.
- Try increasing `k` — *"search ... with k=15."*

### "`status=error: no text extracted`"
The HTML extractor couldn't find anything. Common causes:
- JavaScript-rendered page (no static content)
- Very unusual layout that confuses trafilatura
- An error page returned with a 200 status

Try re-adding with `extract_mode=full`. If that also returns nothing, the
page probably has no static text content.

### "Refresh keeps failing for one URL"
The remote server may be rate-limiting, requiring a real browser UA, or down.
Check `URLBASE_USER_AGENT` and try a manual `refresh` from Claude to see the
error message.

### "I added the same URL twice"
That's fine — it's de-duplicated by URL. The second call updates tags and
extract mode but doesn't create a second copy.

### "My database is huge"
Each chunk stores its text plus a 384-float vector (~1.5 KB). 10,000
chunks ≈ 15 MB. If it's much bigger, look at oversized documents:
*"List my URLs sorted by byte size."* (Claude will read the JSON and sort.)

---

## FAQ

**Does anything leave my machine?**
Only the HTTP requests to fetch your URLs. Embedding and search are 100% local.

**Can I use a different embedding model?**
Yes — set `URLBASE_EMBED_MODEL` to any model name supported by
[`fastembed`](https://qdrant.github.io/fastembed/examples/Supported_Models/).
Multilingual? Try `intfloat/multilingual-e5-base`. Just remember the
dimension lock-in.

**Can I use OpenAI / Voyage / Cohere embeddings?**
Not in this version — the `embed` module would need a small adapter. The
hooks are there; it's a 30-line change.

**Can I share my index between machines?**
Yes — just copy the SQLite file (and the WAL/SHM files if they exist).
The embedding model has to match on both ends.

**What about authenticated URLs / cookies / API keys?**
Not supported. By design — this is a public-URL tool.

**How do I uninstall?**
Remove the venv and the database directory (`~/.local/share/urlbase-mcp/`
by default). The model cache lives in `~/.cache/huggingface/` and
`~/.cache/fastembed/`.

**Why is my reranker score sometimes negative?**
Cross-encoder scores are raw logits, not probabilities. Sign doesn't matter
on its own — only the *relative* ordering does. A negative score for the
top hit is fine if it's the highest among candidates.

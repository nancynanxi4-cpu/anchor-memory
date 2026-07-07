"""
Anchor Memory System — MCP Server

Exposes Anchor Memory as an MCP (Model Context Protocol) server.
Any MCP-compatible client (Claude Code, claude.ai, LobeHub, SillyTavern)
can connect and use graph-structured memory with Hebbian learning.

Supports both stdio and streamable-http transports.

Usage:
    # stdio (default, for Claude Code / terminal)
    python anchor_mcp.py --db-path ./my_memory

    # streamable-http (for web clients, remote access)
    python anchor_mcp.py --transport streamable-http --port 3333
"""

import sys
import os
import uuid
import argparse
import threading
import time
import logging

if sys.platform == "win32" or (hasattr(sys.stdout, 'buffer') and sys.stdout.encoding and sys.stdout.encoding.upper() != 'UTF-8'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.provider import TokenVerifier, AccessToken
from mcp.server.auth.settings import AuthSettings
from anchor_memory import AnchorMemory


class StaticTokenVerifier(TokenVerifier):
    """Validate Bearer tokens against a pre-configured static token."""

    def __init__(self, token: str):
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token == self._token:
            return AccessToken(
                token=token,
                client_id="static",
                scopes=[],
            )
        return None


def create_mcp_server(
    mem: AnchorMemory,
    host: str = "127.0.0.1",
    port: int = 8000,
    auth_token: str | None = None,
) -> FastMCP:
    """Create and return a FastMCP server with all Anchor Memory tools registered."""

    kwargs: dict = {"name": "anchor-memory", "json_response": True, "host": host, "port": port}
    if auth_token:
        base_url = f"http://{host}:{port}"
        kwargs["token_verifier"] = StaticTokenVerifier(auth_token)
        kwargs["auth"] = AuthSettings(
            issuer_url=base_url,
            resource_server_url=base_url,
        )

    mcp = FastMCP(**kwargs)

    @mcp.tool()
    def store_memory(
        text: str,
        tag: str = "general",
        tier: str = "long",
        emotion_score: float = 0.5,
        connect_to: list[str] | None = None,
    ) -> dict:
        """Store a new memory. Memories are nodes in a graph — they can be connected to other memories and carry emotional weight."""
        mid = f"mem_{uuid.uuid4().hex[:8]}"
        mem.store(
            memory_id=mid,
            text=text,
            tag=tag,
            tier=tier,
            emotion_score=emotion_score,
            connect_to=connect_to,
        )
        return {"memory_id": mid, "status": "stored"}

    @mcp.tool()
    def search_memory(
        query: str,
        n: int = 5,
        tag: str | None = None,
        associate: bool = True,
        hebbian: bool = True,
        debug: bool = False,
    ) -> dict:
        """Search memories. Returns results ranked by semantic similarity, citation count, and emotion score. Triggers Hebbian learning — memories retrieved together form connections."""
        results = mem.search(
            query=query,
            n_results=n,
            tag=tag,
            associate=associate,
            hebbian=hebbian,
            debug=debug,
        )
        return {"memories": results}

    @mcp.tool()
    def search_multi(
        queries: list[str],
        n_results_per_query: int = 5,
        n_total: int | None = None,
        tag: str | None = None,
        associate: bool = True,
        hebbian: bool = True,
        include_context: bool = False,
    ) -> dict:
        """Run multiple independent searches and merge results. Use when a single user message contains several distinct topics — vector similarity on the whole message dilutes any one topic, so you pre-split the message into intent strings and pass them here. Each intent searches separately at depth, then results are merged and dedup'd by memory_id. Hebbian co-activation fires across the merged set, so memories surfaced by different intents in the same message form edges with each other."""
        results = mem.search_multi(
            queries=queries,
            n_results_per_query=n_results_per_query,
            n_total=n_total,
            tag=tag,
            associate=associate,
            hebbian=hebbian,
            include_context=include_context,
        )
        return {"memories": results}

    @mcp.tool()
    def connect_memories(
        source_id: str,
        target_id: str,
        weight: float = 2.0,
    ) -> dict:
        """Explicitly connect two memories. Creates a weighted bidirectional edge (synapse). Use for manual entanglement — connecting memories you know are related."""
        mem.db.connect(source_id, target_id, weight=weight)
        return {"status": "connected"}

    @mcp.tool()
    def get_neighbors(
        memory_id: str,
        min_weight: float = 0.5,
        limit: int = 5,
    ) -> dict:
        """Get memories connected to a given memory via graph edges. Returns neighbors sorted by edge weight."""
        neighbors = mem.db.get_neighbors(memory_id, min_weight=min_weight, limit=limit)
        return {"neighbors": [dict(n) for n in neighbors]}

    @mcp.tool()
    def delete_memory(memory_id: str) -> dict:
        """Delete a memory and all its edges."""
        success = mem.delete(memory_id)
        return {"status": "deleted" if success else "not_found"}

    @mcp.tool()
    def dream_pass() -> dict:
        """Run memory consolidation — like sleep for the brain. Decays old memories, prunes weak connections, discovers new ones, equilibrates emotion scores. Run daily."""
        stats = mem.dream_pass()
        return {"status": "complete", **stats}

    @mcp.tool()
    def set_emotion(memory_id: str, score: float) -> dict:
        """Set the emotion score of an existing memory."""
        mem.db.set_emotion_score(memory_id, score)
        return {"status": "updated"}

    @mcp.tool()
    def set_tier(memory_id: str, tier: str) -> dict:
        """Change the tier of an existing memory (core/long/short)."""
        mem.db.set_tier(memory_id, tier)
        return {"status": "updated"}

    @mcp.tool()
    def graph_stats() -> dict:
        """Get overview stats: total memories, edges, tag distribution, tier distribution, top connected nodes."""
        db_count = mem.db.count()
        index_count = mem.count()
        all_mems = mem.db.list_all(limit=db_count)
        tags: dict[str, int] = {}
        tiers: dict[str, int] = {}
        for m in all_mems:
            t = m.get("tag", "unknown")
            tr = m.get("tier", "unknown")
            tags[t] = tags.get(t, 0) + 1
            tiers[tr] = tiers.get(tr, 0) + 1
        return {
            "total_memories": db_count,
            "index_count": index_count,
            "index_synced": db_count == index_count,
            "tags": tags,
            "tiers": tiers,
        }

    @mcp.tool()
    def repair_index() -> dict:
        """Clean up ghost/zombie records between ChromaDB and SQLite. Deletes ghost vectors (ChromaDB only), re-embeds zombie records (SQLite only), and cleans orphan edges/comments/annotations. Safe to run anytime — operations are idempotent."""
        return mem.repair_index()

    @mcp.tool()
    def annotate_memory(memory_id: str, text: str) -> dict:
        """Add an annotation to a memory. Annotations are append-only — they record how understanding of a memory evolves over time. Searchable. Original memory text is never changed."""
        aid = mem.db.annotate(memory_id, text)
        return {"annotation_id": aid, "status": "annotated"}

    @mcp.tool()
    def get_annotations(memory_id: str) -> dict:
        """Get all annotations for a memory, oldest first."""
        anns = mem.db.get_annotations(memory_id)
        return {"annotations": anns}

    @mcp.tool()
    def consolidate(conversation_text: str) -> dict:
        """Passive Hebbian update — after a conversation, pass key topics to build connections between memories that co-occurred but weren't explicitly searched. Zero LLM token cost. Call at the end of a conversation or session."""
        return mem.consolidate(conversation_text)

    @mcp.tool()
    def store_visual(
        text: str,
        visual_embedding: str | None = None,
        tag: str = "visual",
        connect_to: list[str] | None = None,
    ) -> dict:
        """Store a visual observation as a memory with CLIP embedding. For Anchor Vision integration — lets the system remember what it has seen."""
        mid = f"vis_{uuid.uuid4().hex[:8]}"
        mem.store(
            memory_id=mid,
            text=text,
            tag=tag,
            tier="long",
            emotion_score=0.3,
            connect_to=connect_to,
        )
        if visual_embedding:
            mem.db.set_visual_embedding(mid, visual_embedding)
        return {"memory_id": mid, "status": "stored"}

    @mcp.tool()
    def wakeup(
        n_high_emotion: int = 5,
        n_random: int = 2,
        high_emotion_days: int = 3,
    ) -> dict:
        """One-call cold start. Returns pinned memories + recent high-emotion + random old + unread comments. Use at the start of a new conversation/window to ground context. Does NOT mark unread comments as read — call mark_comments_read separately after processing them."""
        return mem.db.wakeup(
            n_high_emotion=n_high_emotion,
            n_random=n_random,
            high_emotion_days=high_emotion_days,
        )

    @mcp.tool()
    def leave_comment(
        memory_id: str,
        content: str,
        author: str = "ai",
        reply_to: str | None = None,
    ) -> dict:
        """Leave a comment on a memory. The primary mechanism for cross-window messaging — comments left here will surface in the next instance's wakeup() call as unread. Useful for leaving context, decisions, or messages for future-you."""
        cid = mem.db.insert_comment(
            memory_id=memory_id,
            content=content,
            author=author,
            reply_to=reply_to,
        )
        return {"comment_id": cid, "status": "inserted"}

    @mcp.tool()
    def get_comments(memory_id: str) -> dict:
        """Get all comments on a specific memory (both read and unread). Use this to read the full conversation thread on a memory."""
        rows = mem.db.get_comments(memory_id)
        return {"comments": [dict(r) for r in rows]}

    @mcp.tool()
    def mark_comments_read(
        comment_ids: list[str],
        reader: str = "ai",
    ) -> dict:
        """Mark comments as read so they don't reappear in next wakeup. Call after processing the unread comments returned by wakeup()."""
        mem.db.mark_comments_read(comment_ids, reader=reader)
        return {"status": "marked", "count": len(comment_ids)}

    @mcp.tool()
    def pin_memory(memory_id: str) -> dict:
        """Pin a memory as core/identity-level. Pinned memories are returned first by wakeup() — use this for memories that should always be loaded at cold start (identity rules, key facts, important relationships)."""
        mem.db.pin(memory_id)
        return {"status": "pinned", "memory_id": memory_id}

    @mcp.tool()
    def unpin_memory(memory_id: str) -> dict:
        """Remove pinned status from a memory. The memory remains in storage but stops appearing in wakeup()'s pinned section."""
        mem.db.unpin(memory_id)
        return {"status": "unpinned", "memory_id": memory_id}

    @mcp.tool()
    def search_annotations(query: str, limit: int = 5) -> dict:
        """Search across annotation text on memories. Returns matching memory_ids and the annotations themselves. Use when looking for memories by what was added to them later (commentary, corrections, additions)."""
        rows = mem.db.search_annotations(query, limit=limit)
        return {"results": [dict(r) for r in rows]}

    @mcp.tool()
    def cite_memory(memory_id: str) -> dict:
        """Increment a memory's usage count to mark that it informed your current reasoning. Most retrievals auto-cite, but use this when you're using a memory's content without doing an explicit search (e.g., recalling from context, weaving older memory into current answer)."""
        mem.db.cite(memory_id)
        return {"status": "cited", "memory_id": memory_id}

    return mcp


def _dream_loop(mem, interval_hours: int = 24):
    log = logging.getLogger("anchor_dream")
    while True:
        time.sleep(interval_hours * 3600)
        try:
            stats = mem.dream_pass()
            log.info("auto dream_pass: %s", stats)
        except Exception as e:
            log.error("auto dream_pass failed: %s", e, exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anchor Memory MCP Server")
    parser.add_argument("--db-path", default="./anchor_data", help="Path to store memory data")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for streamable-http (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port for streamable-http (default: 8000)")
    parser.add_argument("--auth-token", default=None, help="Bearer token (env: ANCHOR_API_KEY)")
    args = parser.parse_args()

    transport = args.transport if args.transport != "stdio" else os.environ.get("ANCHOR_TRANSPORT", "stdio")
    host = args.host if args.host != "127.0.0.1" else os.environ.get("ANCHOR_HOST", "127.0.0.1")
    port = args.port if args.port != 8000 else int(os.environ.get("ANCHOR_PORT", "8000"))
    auth_token = args.auth_token or os.environ.get("ANCHOR_API_KEY")

    os.makedirs(args.db_path, exist_ok=True)
    mem = AnchorMemory(db_path=args.db_path)

    mcp = create_mcp_server(mem, host=host, port=port, auth_token=auth_token)

    if transport == "streamable-http":
        t = threading.Thread(target=_dream_loop, args=(mem,), daemon=True)
        t.start()
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    mcp.run(transport=transport)

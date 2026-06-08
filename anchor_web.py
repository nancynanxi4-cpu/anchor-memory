"""
Anchor Memory — Web UI

Single-file Flask app for browsing and managing anchor memories.
Start: python anchor_web.py --db-path ./anchor_data --port 5000
Password via env: ANCHOR_WEB_PASSWORD (default: 'anchor')
"""

import os
import sys
import uuid
import argparse
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, session, send_file

sys.path.insert(0, os.path.dirname(__file__))
from anchor_memory import AnchorMemory

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def create_app(db_path: str, secret_key: str = None) -> Flask:
    os.makedirs(db_path, exist_ok=True)
    mem = AnchorMemory(db_path=db_path)

    app = Flask(__name__, static_folder=None)
    app.secret_key = secret_key or os.urandom(24).hex()

    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("authenticated"):
                return jsonify({"error": "未授权"}), 401
            return f(*args, **kwargs)
        return wrapper

    @app.route("/")
    def index():
        return send_file(os.path.join(WEB_DIR, "index.html"))

    @app.route("/api/login", methods=["POST"])
    def login():
        data = request.get_json(force=True)
        password = data.get("password", "")
        expected = os.environ.get("ANCHOR_WEB_PASSWORD", "anchor")
        if password == expected:
            session["authenticated"] = True
            return jsonify({"ok": True})
        return jsonify({"error": "密码错误"}), 403

    @app.route("/api/logout", methods=["POST"])
    def logout():
        session.pop("authenticated", None)
        return jsonify({"ok": True})

    @app.route("/api/memories", methods=["GET"])
    @login_required
    def list_memories():
        tier = request.args.get("tier")
        tag = request.args.get("tag")
        sort = request.args.get("sort", "timestamp")
        order = request.args.get("order", "desc")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 30))
        search = request.args.get("q", "").strip()

        conn = mem.db._conn()
        allowed_sorts = {"timestamp", "emotion_score", "usage_count"}
        sort_col = sort if sort in allowed_sorts else "timestamp"
        sort_dir = "DESC" if order == "desc" else "ASC"

        where_clauses = []
        params = []

        if tier:
            where_clauses.append("tier = ?")
            params.append(tier)
        if tag:
            where_clauses.append("tag = ?")
            params.append(tag)
        if search:
            where_clauses.append("text LIKE ?")
            params.append(f"%{search}%")

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        total = conn.execute(f"SELECT COUNT(*) FROM memories{where_sql}", params).fetchone()[0]

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT memory_id, text, timestamp, tag, tier, pinned, emotion_score, "
            f"usage_count, last_used FROM memories{where_sql} "
            f"ORDER BY pinned DESC, {sort_col} {sort_dir} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

        ann_counts = {}
        com_counts = {}
        memory_ids = [r["memory_id"] for r in rows]

        if memory_ids:
            placeholders = ",".join("?" * len(memory_ids))
            for row in conn.execute(
                f"SELECT memory_id, COUNT(*) as cnt FROM annotations "
                f"WHERE memory_id IN ({placeholders}) GROUP BY memory_id",
                memory_ids,
            ).fetchall():
                ann_counts[row["memory_id"]] = row["cnt"]
            for row in conn.execute(
                f"SELECT memory_id, COUNT(*) as cnt FROM comments "
                f"WHERE memory_id IN ({placeholders}) GROUP BY memory_id",
                memory_ids,
            ).fetchall():
                com_counts[row["memory_id"]] = row["cnt"]

        conn.close()

        items = []
        for r in rows:
            items.append({
                "memory_id": r["memory_id"],
                "text": r["text"],
                "timestamp": r["timestamp"],
                "tag": r["tag"],
                "tier": r["tier"],
                "pinned": bool(r["pinned"]),
                "emotion_score": r["emotion_score"],
                "usage_count": r["usage_count"] or 0,
                "last_used": r["last_used"],
                "has_annotation": ann_counts.get(r["memory_id"], 0) > 0,
                "has_comment": com_counts.get(r["memory_id"], 0) > 0,
                "annotation_count": ann_counts.get(r["memory_id"], 0),
                "comment_count": com_counts.get(r["memory_id"], 0),
            })

        tags_rows = mem.db._conn().execute("SELECT DISTINCT tag FROM memories ORDER BY tag").fetchall()
        tiers_rows = mem.db._conn().execute("SELECT DISTINCT tier FROM memories ORDER BY tier").fetchall()

        return jsonify({
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "tags": [r["tag"] for r in tags_rows],
            "tiers": [r["tier"] for r in tiers_rows],
        })

    @app.route("/api/memories/<memory_id>", methods=["GET"])
    @login_required
    def get_memory(memory_id):
        row = mem.db.get(memory_id)
        if not row:
            return jsonify({"error": "未找到"}), 404

        annotations = mem.db.get_annotations(memory_id)
        comments = mem.db.get_comments(memory_id)
        neighbors = mem.db.get_neighbors(memory_id, min_weight=0.0, limit=50)

        neighbor_details = []
        conn = mem.db._conn()
        for nb in neighbors:
            nb_row = conn.execute(
                "SELECT memory_id, text, tag, tier FROM memories WHERE memory_id = ?",
                (nb["memory_id"],),
            ).fetchone()
            if nb_row:
                neighbor_details.append({
                    "memory_id": nb["memory_id"],
                    "text": nb_row["text"][:120],
                    "tag": nb_row["tag"],
                    "tier": nb_row["tier"],
                    "weight": nb["weight"],
                })
        conn.close()

        return jsonify({
            "memory": dict(row),
            "annotations": [dict(a) for a in annotations],
            "comments": [dict(c) for c in comments],
            "neighbors": neighbor_details,
        })

    @app.route("/api/memories/<memory_id>", methods=["PUT"])
    @login_required
    def update_memory(memory_id):
        data = request.get_json(force=True)
        row = mem.db.get(memory_id)
        if not row:
            return jsonify({"error": "未找到"}), 404

        if "text" in data and data["text"].strip():
            new_text = data["text"].strip()
            embedding = mem._embedder.encode(new_text).tolist()
            meta = {
                "memory_id": memory_id,
                "timestamp": row.get("timestamp", datetime.utcnow().isoformat()),
                "tag": data.get("tag", row.get("tag", "general")),
            }
            mem._collection.upsert(
                ids=[memory_id],
                embeddings=[embedding],
                documents=[new_text],
                metadatas=[meta],
            )
            conn = mem.db._conn()
            conn.execute(
                "UPDATE memories SET text = ? WHERE memory_id = ?",
                (new_text, memory_id),
            )
            conn.commit()
            conn.close()

        if "tier" in data:
            mem.db.set_tier(memory_id, data["tier"])

        if "emotion_score" in data:
            mem.db.set_emotion_score(memory_id, float(data["emotion_score"]))

        if "pinned" in data:
            if data["pinned"]:
                mem.db.pin(memory_id)
            else:
                mem.db.unpin(memory_id)

        return jsonify({"ok": True})

    @app.route("/api/memories/<memory_id>", methods=["DELETE"])
    @login_required
    def delete_memory(memory_id):
        success = mem.delete(memory_id)
        if success:
            return jsonify({"ok": True})
        return jsonify({"error": "未找到"}), 404

    @app.route("/api/memories/<memory_id>/pin", methods=["POST"])
    @login_required
    def pin_memory(memory_id):
        mem.db.pin(memory_id)
        return jsonify({"ok": True})

    @app.route("/api/memories/<memory_id>/unpin", methods=["POST"])
    @login_required
    def unpin_memory(memory_id):
        mem.db.unpin(memory_id)
        return jsonify({"ok": True})

    @app.route("/api/edges", methods=["POST"])
    @login_required
    def create_edge():
        data = request.get_json(force=True)
        source_id = data.get("source_id", "").strip()
        target_id = data.get("target_id", "").strip()
        weight = float(data.get("weight", 2.0))

        if not source_id or not target_id:
            return jsonify({"error": "需要 source_id 和 target_id"}), 400

        src = mem.db.get(source_id)
        tgt = mem.db.get(target_id)
        if not src:
            return jsonify({"error": f"源记忆 {source_id} 不存在"}), 404
        if not tgt:
            return jsonify({"error": f"目标记忆 {target_id} 不存在"}), 404

        mem.db.connect(source_id, target_id, weight=weight)
        return jsonify({"ok": True})

    @app.route("/api/edges", methods=["DELETE"])
    @login_required
    def delete_edge():
        data = request.get_json(force=True)
        source_id = data.get("source_id", "").strip()
        target_id = data.get("target_id", "").strip()

        if not source_id or not target_id:
            return jsonify({"error": "需要 source_id 和 target_id"}), 400

        conn = mem.db._conn()
        conn.execute(
            "DELETE FROM edges WHERE source_id = ? AND target_id = ?",
            (source_id, target_id),
        )
        conn.execute(
            "DELETE FROM edges WHERE source_id = ? AND target_id = ?",
            (target_id, source_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/memories/<memory_id>/comments", methods=["GET"])
    @login_required
    def get_comments(memory_id):
        comments = mem.db.get_comments(memory_id)
        return jsonify({"comments": [dict(c) for c in comments]})

    @app.route("/api/memories/<memory_id>/comments", methods=["POST"])
    @login_required
    def add_comment(memory_id):
        data = request.get_json(force=True)
        content = data.get("content", "").strip()
        if not content:
            return jsonify({"error": "评论内容不能为空"}), 400
        cid = mem.db.insert_comment(memory_id, content, author="human")
        return jsonify({"comment_id": cid, "ok": True})

    @app.route("/api/memories/<memory_id>/annotations", methods=["GET"])
    @login_required
    def get_annotations(memory_id):
        annotations = mem.db.get_annotations(memory_id)
        return jsonify({"annotations": [dict(a) for a in annotations]})

    @app.route("/api/stats", methods=["GET"])
    @login_required
    def stats():
        total = mem.count()
        conn = mem.db._conn()
        tags = {}
        tiers = {}
        for row in conn.execute("SELECT tag, COUNT(*) as cnt FROM memories GROUP BY tag"):
            tags[row["tag"]] = row["cnt"]
        for row in conn.execute("SELECT tier, COUNT(*) as cnt FROM memories GROUP BY tier"):
            tiers[row["tier"]] = row["cnt"]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        pinned_count = conn.execute("SELECT COUNT(*) FROM memories WHERE pinned = 1").fetchone()[0]
        conn.close()
        return jsonify({
            "total_memories": total,
            "total_edges": edge_count,
            "pinned_count": pinned_count,
            "tags": tags,
            "tiers": tiers,
        })

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anchor Memory Web UI")
    parser.add_argument("--db-path", default="./anchor_data", help="数据库路径")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=5000, help="端口")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    password = os.environ.get("ANCHOR_WEB_PASSWORD", "anchor")
    print(f"Anchor Memory Web UI — http://{args.host}:{args.port}")
    print(f"密码: {'(环境变量 ANCHOR_WEB_PASSWORD)' if os.environ.get('ANCHOR_WEB_PASSWORD') else 'anchor (默认)'}")

    app = create_app(args.db_path)
    app.run(host=args.host, port=args.port, debug=args.debug)

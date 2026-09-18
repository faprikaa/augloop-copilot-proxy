#!/usr/bin/env python3
"""
conversation_store.py - Conversation Persistence (SQLite)

Provides OpenAI-compatible conversation management:
  1. Create / delete / list conversations
  2. Add messages (user/assistant/system/tool)
  3. Retrieve conversation history
  4. Support tool_calls storage

Schema:
  conversations: id, title, created_at, updated_at, model
  messages: id, conversation_id, role, content, tool_calls, tool_call_id, created_at

Usage:
    store = ConversationStore("conversations.db")
    conv_id = store.create_conversation("My Chat")
    store.add_message(conv_id, "user", "Hello")
    msgs = store.get_messages(conv_id)
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("conversations")


class ConversationStore:
    """SQLite conversation store (thread-safe, persistent connection)"""

    def __init__(self, db_path: str = "conversations.db"):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT 'copilot',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    name TEXT,
                    created_at REAL NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id, seq);
            """)

    # ── Conversation Management ─────────────────────────────────────────────

    def create_conversation(
        self,
        title: str = "",
        model: str = "copilot",
    ) -> str:
        """Create a new conversation and return conversation_id"""
        conv_id = f"conv-{uuid.uuid4().hex[:24]}"
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conv_id, title or "New Conversation", model, now, now),
            )
            self._conn.commit()
        logger.info("Created conversation: %s", conv_id)
        return conv_id

    def delete_conversation(self, conv_id: str) -> bool:
        """Delete conversation and all its messages"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def get_conversation(self, conv_id: str) -> dict | None:
        """Get conversation information"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def list_conversations(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List conversations"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as message_count "
                "FROM conversations c ORDER BY c.updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_conversation_title(self, conv_id: str, title: str):
        """Update conversation title"""
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, time.time(), conv_id),
            )
            self._conn.commit()

    def touch_conversation(self, conv_id: str):
        """Update conversation updated_at timestamp"""
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (time.time(), conv_id),
            )
            self._conn.commit()

    # ── Message Management ──────────────────────────────────────────────────

    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str = "",
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> str:
        """
        Add a message to the conversation

        Args:
            conv_id: Conversation ID
            role: user / assistant / system / tool
            content: Message content
            tool_calls: Tool call list requested by AI (OpenAI format)
            tool_call_id: Tool response ID corresponding to call
            name: Tool name (when role=tool)

        Returns:
            message_id
        """
        msg_id = f"msg-{uuid.uuid4().hex[:24]}"
        now = time.time()

        # Get next seq
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(seq) as max_seq FROM messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            seq = (row["max_seq"] or 0) + 1

            self._conn.execute(
                """INSERT INTO messages
                   (id, conversation_id, role, content, tool_calls, tool_call_id, name, created_at, seq)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg_id,
                    conv_id,
                    role,
                    content,
                    json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                    tool_call_id,
                    name,
                    now,
                    seq,
                ),
            )
            # Update conversation updated timestamp
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id),
            )
            self._conn.commit()

        return msg_id

    def get_messages(self, conv_id: str) -> list[dict]:
        """Get all messages in conversation (OpenAI format)"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC",
                (conv_id,),
            ).fetchall()

        messages = []
        for row in rows:
            msg = {
                "role": row["role"],
                "content": row["content"],
            }
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            if row["name"]:
                msg["name"] = row["name"]
            messages.append(msg)

        return messages

    def get_message_count(self, conv_id: str) -> int:
        """Get count of messages in conversation"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            return row["cnt"]

    def clear_messages(self, conv_id: str):
        """Clear conversation messages (retains conversation metadata)"""
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (time.time(), conv_id),
            )
            self._conn.commit()

    # ── Convenience Methods ─────────────────────────────────────────────────

    def get_or_create_conversation(self, conv_id: str | None = None, model: str = "copilot") -> str:
        """Get or create conversation"""
        if conv_id:
            conv = self.get_conversation(conv_id)
            if conv:
                return conv_id
        return self.create_conversation(model=model)

    def to_openai_messages(self, conv_id: str) -> list[dict]:
        """Get list of messages in OpenAI API format"""
        return self.get_messages(conv_id)

    def search_conversations(self, query: str, limit: int = 20) -> list[dict]:
        """Search conversation titles and message contents"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT DISTINCT c.* FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.title LIKE ? OR m.content LIKE ?
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def close(self):
        """Close database connection"""
        with self._lock:
            self._conn.close()

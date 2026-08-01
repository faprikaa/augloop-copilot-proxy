#!/usr/bin/env python3
"""
conversation_store.py - 对话持久化 (SQLite)

提供 OpenAI 兼容的对话管理:
  1. 创建/删除/列出对话
  2. 添加消息 (user/assistant/tool)
  3. 获取对话历史
  4. 支持 tool_calls 存储

表结构:
  conversations: id, title, created_at, updated_at, model
  messages: id, conversation_id, role, content, tool_calls, tool_call_id, created_at

用法:
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
    """SQLite 对话存储 (线程安全，持久连接)"""

    def __init__(self, db_path: str = "conversations.db"):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()
        logger.info("ConversationStore initialized: %s", self.db_path)

    def _init_db(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT DEFAULT '',
                    model TEXT DEFAULT 'copilot',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    tool_calls TEXT DEFAULT NULL,
                    tool_call_id TEXT DEFAULT NULL,
                    name TEXT DEFAULT NULL,
                    created_at REAL NOT NULL,
                    seq INTEGER NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id, seq);
            """)

    # ── 对话管理 ────────────────────────────────────────────────────────────

    def create_conversation(
        self,
        title: str = "",
        model: str = "copilot",
    ) -> str:
        """创建新对话，返回 conversation_id"""
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
        """删除对话及其所有消息"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def get_conversation(self, conv_id: str) -> dict | None:
        """获取对话信息"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def list_conversations(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """列出对话"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as message_count "
                "FROM conversations c ORDER BY c.updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_conversation_title(self, conv_id: str, title: str):
        """更新对话标题"""
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, time.time(), conv_id),
            )
            self._conn.commit()

    def touch_conversation(self, conv_id: str):
        """更新对话的 updated_at"""
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (time.time(), conv_id),
            )
            self._conn.commit()

    # ── 消息管理 ────────────────────────────────────────────────────────────

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
        添加消息到对话

        Args:
            conv_id: 对话 ID
            role: user / assistant / system / tool
            content: 消息内容
            tool_calls: AI 请求的工具调用列表 (OpenAI 格式)
            tool_call_id: 工具响应对应的调用 ID
            name: 工具名称 (role=tool 时)

        Returns:
            message_id
        """
        msg_id = f"msg-{uuid.uuid4().hex[:24]}"
        now = time.time()

        # 获取下一个 seq
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
            # 更新对话时间
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id),
            )
            self._conn.commit()

        return msg_id

    def get_messages(self, conv_id: str) -> list[dict]:
        """获取对话的所有消息 (OpenAI 格式)"""
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
        """获取对话消息数"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            return row["cnt"]

    def clear_messages(self, conv_id: str):
        """清空对话消息 (保留对话)"""
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (time.time(), conv_id),
            )
            self._conn.commit()

    # ── 便利方法 ────────────────────────────────────────────────────────────

    def get_or_create_conversation(self, conv_id: str | None = None, model: str = "copilot") -> str:
        """获取或创建对话"""
        if conv_id:
            conv = self.get_conversation(conv_id)
            if conv:
                return conv_id
        return self.create_conversation(model=model)

    def to_openai_messages(self, conv_id: str) -> list[dict]:
        """获取 OpenAI API 格式的消息列表"""
        return self.get_messages(conv_id)

    def search_conversations(self, query: str, limit: int = 20) -> list[dict]:
        """搜索对话标题和消息内容"""
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
        """关闭数据库连接"""
        with self._lock:
            self._conn.close()

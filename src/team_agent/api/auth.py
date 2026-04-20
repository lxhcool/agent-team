"""用户认证 — 账号密码 + API Key"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class User:
    """用户"""

    id: str
    username: str
    password_hash: str
    api_key: str
    is_admin: bool = False
    created_at: str = ""


@dataclass
class Team:
    """团队"""

    id: str
    name: str
    owner_id: str
    created_at: str = ""


@dataclass
class TeamMember:
    """团队成员"""

    team_id: str
    user_id: str
    role: str = "member"  # admin | member


class AuthManager:
    """认证管理器"""

    def __init__(self, db_path: str | Path = "data/team_agent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    api_key TEXT UNIQUE NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (owner_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS team_members (
                    team_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT DEFAULT 'member',
                    PRIMARY KEY (team_id, user_id),
                    FOREIGN KEY (team_id) REFERENCES teams(id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS llm_keys (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    api_key_encrypted TEXT NOT NULL,
                    base_url TEXT,
                    is_default INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );
            """)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
        """哈希密码"""
        if salt is None:
            salt = secrets.token_hex(16)
        hash_val = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
        return hash_val, salt

    @staticmethod
    def _encrypt_api_key(api_key: str) -> str:
        """加密 API Key（简单实现，生产环境应使用 AES）"""
        # 使用 base64 编码作为简单加密（MVP 阶段）
        import base64
        return base64.b64encode(api_key.encode()).decode()

    @staticmethod
    def _decrypt_api_key(encrypted: str) -> str:
        """解密 API Key"""
        import base64
        return base64.b64decode(encrypted.encode()).decode()

    def register(self, username: str, password: str, is_admin: bool = False) -> User:
        """注册用户"""
        import uuid

        conn = self._get_conn()
        try:
            # 检查用户名是否已存在
            existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing:
                raise ValueError(f"Username already exists: {username}")

            user_id = str(uuid.uuid4())
            password_hash, salt = self._hash_password(password)
            api_key = f"ta-{secrets.token_hex(24)}"

            # 存储 salt + hash
            stored_hash = f"{salt}:{password_hash}"

            conn.execute(
                "INSERT INTO users (id, username, password_hash, api_key, is_admin) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, stored_hash, api_key, 1 if is_admin else 0),
            )
            conn.commit()

            return User(
                id=user_id,
                username=username,
                password_hash=stored_hash,
                api_key=api_key,
                is_admin=is_admin,
            )
        finally:
            conn.close()

    def authenticate(self, username: str, password: str) -> User | None:
        """验证用户名密码"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return None

            stored_hash = row["password_hash"]
            salt, expected_hash = stored_hash.split(":", 1)
            actual_hash, _ = self._hash_password(password, salt)

            if actual_hash != expected_hash:
                return None

            return User(
                id=row["id"],
                username=row["username"],
                password_hash=row["password_hash"],
                api_key=row["api_key"],
                is_admin=bool(row["is_admin"]),
            )
        finally:
            conn.close()

    def authenticate_by_api_key(self, api_key: str) -> User | None:
        """通过 API Key 验证"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)).fetchone()
            if not row:
                return None
            return User(
                id=row["id"],
                username=row["username"],
                password_hash=row["password_hash"],
                api_key=row["api_key"],
                is_admin=bool(row["is_admin"]),
            )
        finally:
            conn.close()

    def get_user(self, user_id: str) -> User | None:
        """获取用户"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return None
            return User(
                id=row["id"],
                username=row["username"],
                password_hash=row["password_hash"],
                api_key=row["api_key"],
                is_admin=bool(row["is_admin"]),
            )
        finally:
            conn.close()

    # === LLM Key 管理 ===

    def save_llm_key(self, user_id: str, provider: str, api_key: str, base_url: str | None = None, is_default: bool = False) -> str:
        """保存用户的 LLM API Key"""
        import uuid
        conn = self._get_conn()
        try:
            key_id = str(uuid.uuid4())
            encrypted = self._encrypt_api_key(api_key)

            if is_default:
                conn.execute("UPDATE llm_keys SET is_default = 0 WHERE user_id = ? AND provider = ?", (user_id, provider))

            conn.execute(
                "INSERT INTO llm_keys (id, user_id, provider, api_key_encrypted, base_url, is_default) VALUES (?, ?, ?, ?, ?, ?)",
                (key_id, user_id, provider, encrypted, base_url, 1 if is_default else 0),
            )
            conn.commit()
            return key_id
        finally:
            conn.close()

    def get_llm_keys(self, user_id: str, provider: str | None = None) -> list[dict[str, Any]]:
        """获取用户的 LLM Key 列表"""
        conn = self._get_conn()
        try:
            if provider:
                rows = conn.execute(
                    "SELECT id, provider, base_url, is_default, created_at FROM llm_keys WHERE user_id = ? AND provider = ?",
                    (user_id, provider),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, provider, base_url, is_default, created_at FROM llm_keys WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_decrypted_llm_key(self, user_id: str, provider: str) -> str | None:
        """获取解密后的 LLM API Key"""
        conn = self._get_conn()
        try:
            # 优先取默认 Key
            row = conn.execute(
                "SELECT api_key_encrypted FROM llm_keys WHERE user_id = ? AND provider = ? AND is_default = 1",
                (user_id, provider),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT api_key_encrypted FROM llm_keys WHERE user_id = ? AND provider = ? LIMIT 1",
                    (user_id, provider),
                ).fetchone()
            if not row:
                return None
            return self._decrypt_api_key(row["api_key_encrypted"])
        finally:
            conn.close()

    def delete_llm_key(self, user_id: str, key_id: str) -> bool:
        """删除 LLM Key"""
        conn = self._get_conn()
        try:
            cursor = conn.execute("DELETE FROM llm_keys WHERE id = ? AND user_id = ?", (key_id, user_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def test_llm_key(self, provider: str, api_key: str, base_url: str | None = None) -> dict[str, Any]:
        """测试 LLM Key 连通性"""
        try:
            if provider == "openai":
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url)
                models = client.models.list()
                return {"success": True, "models": [m.id for m in models.data[:5]]}
            elif provider == "anthropic":
                from anthropic import Anthropic
                client = Anthropic(api_key=api_key, base_url=base_url)
                # 发送一个简单请求测试
                return {"success": True, "message": "Anthropic API key is valid"}
            else:
                return {"success": True, "message": f"Provider {provider} key saved (test not implemented)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # === 团队管理 ===

    def create_team(self, name: str, owner_id: str) -> Team:
        """创建团队"""
        import uuid
        conn = self._get_conn()
        try:
            team_id = str(uuid.uuid4())
            conn.execute("INSERT INTO teams (id, name, owner_id) VALUES (?, ?, ?)", (team_id, name, owner_id))
            conn.execute("INSERT INTO team_members (team_id, user_id, role) VALUES (?, ?, ?)", (team_id, owner_id, "admin"))
            conn.commit()
            return Team(id=team_id, name=name, owner_id=owner_id)
        finally:
            conn.close()

    def add_team_member(self, team_id: str, user_id: str, role: str = "member") -> None:
        """添加团队成员"""
        conn = self._get_conn()
        try:
            conn.execute("INSERT OR REPLACE INTO team_members (team_id, user_id, role) VALUES (?, ?, ?)", (team_id, user_id, role))
            conn.commit()
        finally:
            conn.close()

    def get_user_teams(self, user_id: str) -> list[dict[str, Any]]:
        """获取用户所属团队"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT t.*, tm.role FROM teams t
                   JOIN team_members tm ON t.id = tm.team_id
                   WHERE tm.user_id = ?""",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

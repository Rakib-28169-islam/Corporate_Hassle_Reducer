"""SQLite cache/memory layer for Corporate Hassle Reducer.
All agents read/write through DatabaseManager (singleton)."""

import os
import json
import logging
import aiosqlite
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "corporate_hassle_reducer.db"
)

_db_instance = None


class DatabaseManager:
    """Async SQLite manager — JSON-first, UPSERT everywhere, singleton."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH

    async def init_db(self):
        """Create all tables and indexes if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    email TEXT,
                    settings TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS connections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    composio_id TEXT,
                    status TEXT DEFAULT 'disconnected',
                    connected_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    external_id TEXT,
                    content TEXT DEFAULT '{}',
                    cached_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    UNIQUE(user_id, tool, external_id)
                );

                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    response TEXT DEFAULT '{}',
                    route TEXT,
                    agent TEXT,
                    operations TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_memory_lookup
                    ON memory(user_id, tool, data_type);
                CREATE INDEX IF NOT EXISTS idx_memory_external
                    ON memory(external_id);
                CREATE INDEX IF NOT EXISTS idx_chat_user
                    ON chat_history(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_connections_user
                    ON connections(user_id, tool);
            """)
            await db.commit()
            logger.info(f"Database initialized at {self.db_path}")

    def _build_where(self, user_id, tool=None, data_type=None):
        """Build WHERE clause parts for memory queries.
        Returns (conditions_list, params_list).
        """
        conditions = ["user_id = ?"]
        params = [user_id]

        for col, val in [("tool", tool), ("data_type", data_type)]:
            if val is None:
                continue
            if isinstance(val, list):
                placeholders = ", ".join("?" for _ in val)
                conditions.append(f"{col} IN ({placeholders})")
                params.extend(val)
            else:
                conditions.append(f"{col} = ?")
                params.append(val)

        return conditions, params

    async def create_user(self, user_id, name=None, email=None, settings=None):
        """Insert a new user. Returns True on success, False if ID exists."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO users (id, name, email, settings) VALUES (?, ?, ?, ?)",
                    (user_id, name, email, json.dumps(settings or {}))
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def get_user(self, user_id):
        """Fetch a user by ID. Returns dict or None."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "name": row["name"],
                        "email": row["email"],
                        "settings": json.loads(row["settings"] or "{}"),
                        "created_at": row["created_at"],
                    }
                return None

    async def save_connection(self, connection_id, user_id, tool, composio_id=None, status="connected"):
        """UPSERT an OAuth tool connection for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO connections (id, user_id, tool, composio_id, status, connected_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       status = excluded.status,
                       composio_id = excluded.composio_id,
                       connected_at = excluded.connected_at""",
                (connection_id, user_id, tool, composio_id, status,
                 datetime.now(timezone.utc).isoformat())
            )
            await db.commit()

    async def get_connections(self, user_id):
        """Get all tool connections for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM connections WHERE user_id = ?", (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def cache_data(self, user_id, tool, data_type, content, external_id=None):
        """Store one item in the memory cache.
        With external_id uses UPSERT to prevent duplicates.
        """
        content_json = json.dumps(content) if isinstance(content, dict) else content
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            if external_id:
                await db.execute(
                    """INSERT INTO memory (user_id, tool, data_type, external_id, content, cached_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, tool, external_id) DO UPDATE SET
                           content = excluded.content,
                           data_type = excluded.data_type,
                           cached_at = excluded.cached_at""",
                    (user_id, tool, data_type, external_id, content_json, now)
                )
            else:
                await db.execute(
                    "INSERT INTO memory (user_id, tool, data_type, content, cached_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, tool, data_type, content_json, now)
                )
            await db.commit()

    async def cache_batch(self, user_id, tool, data_type, items, id_field="id"):
        """Cache multiple items via repeated cache_data() calls."""
        for item in items:
            external_id = item.get(id_field)
            await self.cache_data(user_id, tool, data_type, item, external_id)

    async def search_memory(self, user_id, tool=None, data_type=None, query=None, limit=50):
        """Search the local memory cache.
        Supports str-or-list for tool/data_type, LIKE text search, newest-first.
        """
        conditions, params = self._build_where(user_id, tool, data_type)

        if query:
            conditions.append("content LIKE ?")
            params.append(f"%{query}%")

        params.append(limit)
        where = " AND ".join(conditions)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM memory WHERE {where} ORDER BY cached_at DESC LIMIT ?",
                params
            ) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "id": row["id"],
                        "tool": row["tool"],
                        "data_type": row["data_type"],
                        "external_id": row["external_id"],
                        "content": json.loads(row["content"] or "{}"),
                        "cached_at": row["cached_at"],
                    })
                return results

    async def execute_sql(self, sql, params=None):
        """Execute a read-only SQL query. Blocks non-SELECT and dangerous keywords."""
        sql_stripped = sql.strip().upper()

        if not sql_stripped.startswith("SELECT"):
            raise ValueError("Only SELECT queries allowed")

        dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "PRAGMA"]
        for word in dangerous:
            if word in sql_stripped:
                raise ValueError(f"Forbidden keyword in query: {word}")

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params or []) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def invalidate_cache(self, user_id, tool=None, data_type=None):
        """Delete cached memory rows matching the given filters."""
        conditions, params = self._build_where(user_id, tool, data_type)
        where = " AND ".join(conditions)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"DELETE FROM memory WHERE {where}", params)
            await db.commit()
            logger.info(f"Cache invalidated: user={user_id}, tool={tool}, type={data_type}")

    async def get_memory_count(self, user_id, tool=None):
        """Count cached items for a user, optionally filtered by tool."""
        conditions, params = self._build_where(user_id, tool)
        where = " AND ".join(conditions)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM memory WHERE {where}", params
            ) as cursor:
                row = await cursor.fetchone()
                return row[0]

    async def log_chat(self, user_id, query, response, route=None, agent=None, operations=None):
        """Append a query/response pair to chat_history."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO chat_history (user_id, query, response, route, agent, operations)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    query,
                    json.dumps(response) if isinstance(response, dict) else response,
                    route,
                    agent,
                    json.dumps(operations) if isinstance(operations, list) else operations,
                )
            )
            await db.commit()

    async def get_chat_history(self, user_id, limit=20):
        """Retrieve last N chat entries for a user, newest first."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM chat_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "id": row["id"],
                        "query": row["query"],
                        "response": json.loads(row["response"] or "{}"),
                        "route": row["route"],
                        "agent": row["agent"],
                        "operations": json.loads(row["operations"] or "[]"),
                        "created_at": row["created_at"],
                    })
                return results

    async def delete_by_user(self, table, column, user_id):
        """Delete all rows matching user_id from a given table. Returns deleted count."""
        allowed = {"users", "connections", "memory", "chat_history"}
        if table not in allowed:
            raise ValueError(f"Table '{table}' not allowed for deletion")

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (user_id,)
            ) as cursor:
                count = (await cursor.fetchone())[0]

            await db.execute(f"DELETE FROM {table} WHERE {column} = ?", (user_id,))
            await db.commit()
            logger.info(f"Deleted {count} rows from {table} for {column}={user_id}")
            return count

    async def get_table_info(self):
        """Get row counts for all tables."""
        async with aiosqlite.connect(self.db_path) as db:
            tables = {}
            for table in ["users", "connections", "memory", "chat_history"]:
                async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    row = await cursor.fetchone()
                    tables[table] = row[0]
            return tables


def get_database():
    """Return the singleton DatabaseManager instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseManager()
    return _db_instance

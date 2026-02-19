"""
=============================================================================
DB MANAGER — The 2nd Brain of Corporate Hassle Reducer
=============================================================================

WHY THIS EXISTS:
    Without this, every user query ("show my emails", "slack messages") hits
    Composio's external API — slow (500ms-2s), costly, and rate-limited.

    This module creates a LOCAL SQLite database that acts as a cache/memory.
    First query → fetches from Composio → stores locally.
    Second query → reads from local DB instantly (< 5ms).

HOW IT WORKS:
    - 4 tables, all using JSON-first design (future-proof, zero migrations)
    - ONE unified 'memory' table for ALL tools (gmail, slack, outlook, etc.)
    - New tool added? Just insert rows with tool="new_tool". No schema change.
    - Singleton pattern: one DatabaseManager instance shared across the app.

TABLE OVERVIEW:
    ┌─────────────────────────────────────────────────────────────┐
    │  users         → Who is using the app                      │
    │  connections   → Which tools (Gmail/Slack) are connected   │
    │  memory        → Cached data from ALL tools (the 2nd brain)│
    │  chat_history  → What the user asked & what we responded   │
    └─────────────────────────────────────────────────────────────┘

USAGE:
    from database import get_database

    db = get_database()           # Get singleton instance
    await db.init_db()            # Create tables (call once on startup)
    await db.cache_data(...)      # Store data from Composio
    await db.search_memory(...)   # Search local cache before hitting API

=============================================================================
"""

import os
import json
import logging
import aiosqlite
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database file path
# Resolves to: backend/corporate_hassle_reducer.db
# Same location that .env's DATABASE_URL points to
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "corporate_hassle_reducer.db"
)

# Singleton — only ONE instance exists across the entire app
_db_instance = None


class DatabaseManager:
    """
    SQLite Database Manager (Singleton).

    This is the LOCAL data layer of the entire backend.
    Every agent (Gmail, Slack, Outlook) reads/writes through this.

    Design Principles:
        1. JSON-first  → content column stores ANY data as JSON string
        2. Zero-migration → new tools = new rows, never new tables
        3. UPSERT everywhere → no duplicate data, always fresh
        4. Async only → uses aiosqlite, never blocks the event loop
        5. Singleton → one instance, shared by all agents and routes

    4 Tables:
        users        → user profiles with JSON settings
        connections  → which OAuth tools are connected per user
        memory       → THE cache — all emails, messages, events stored here
        chat_history → conversation log for context & debugging
    """

    def __init__(self, db_path=None):
        """
        Initialize with a custom path or use the default.

        Args:
            db_path: Optional custom path to .db file.
                     Default = backend/corporate_hassle_reducer.db
                     Pass a custom path for testing (e.g., "test.db")
        """
        self.db_path = db_path or DB_PATH

    # =========================================================================
    # INITIALIZATION — Run once on server startup
    # =========================================================================

    async def init_db(self):
        """
        Create all 4 tables and their indexes if they don't exist.

        Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS.
        Should be called ONCE in FastAPI's startup event:

            @app.on_event("startup")
            async def startup():
                db = get_database()
                await db.init_db()

        Tables Created:
            1. users        — id(PK), name, email, settings(JSON), created_at
            2. connections  — id(PK), user_id(FK), tool, composio_id, status, connected_at
            3. memory       — id(AUTO), user_id(FK), tool, data_type, external_id, content(JSON), cached_at
                              UNIQUE(user_id, tool, external_id) → prevents duplicate cached items
            4. chat_history — id(AUTO), user_id(FK), query, response(JSON), route, agent, operations(JSON), created_at

        Indexes Created (for fast lookups):
            - idx_memory_lookup     → (user_id, tool, data_type) — main search path
            - idx_memory_external   → (external_id) — find by Gmail msg_id, Slack msg_id, etc.
            - idx_chat_user         → (user_id, created_at) — recent chat history
            - idx_connections_user  → (user_id, tool) — user's connected tools
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""

                -- =========================================================
                -- TABLE 1: USERS
                -- Stores user profiles. Settings is a JSON blob for
                -- preferences like theme, notification prefs, etc.
                -- =========================================================
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    email TEXT,
                    settings TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                );

                -- =========================================================
                -- TABLE 2: CONNECTIONS
                -- Tracks which OAuth tools a user has connected.
                -- Example: user_1 connected "gmail" via Composio OAuth
                -- =========================================================
                CREATE TABLE IF NOT EXISTS connections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    composio_id TEXT,
                    status TEXT DEFAULT 'disconnected',
                    connected_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                -- =========================================================
                -- TABLE 3: MEMORY (The 2nd Brain)
                -- Unified cache for ALL tools. This is the core table.
                --
                -- Why ONE table instead of emails_cache, slack_cache, etc?
                --   → New tool = new rows with tool="new_tool". Zero migration.
                --   → One search query can span all tools.
                --   → Simpler code: cache_data() works for any tool.
                --
                -- content column stores the FULL data as JSON:
                --   Gmail email:  {"subject":"...", "from":"...", "body":"..."}
                --   Slack msg:    {"channel":"#dev", "user":"john", "text":"..."}
                --   Outlook event:{"title":"Meeting", "start":"...", "end":"..."}
                --
                -- UNIQUE(user_id, tool, external_id) prevents duplicates:
                --   Same Gmail message_id for same user = UPDATE, not INSERT.
                -- =========================================================
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

                -- =========================================================
                -- TABLE 4: CHAT HISTORY
                -- Logs every user query and the system's response.
                -- Used for: context in follow-up queries, debugging,
                -- analytics (which agents are used most), and future
                -- conversation memory.
                -- =========================================================
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

                -- =========================================================
                -- INDEXES — Speed up the most common queries
                -- Without these, SQLite does full table scans (slow on 10K+ rows)
                -- =========================================================

                -- Memory: "give me all gmail emails for user_1"
                CREATE INDEX IF NOT EXISTS idx_memory_lookup
                    ON memory(user_id, tool, data_type);

                -- Memory: "find the cached item with this Gmail message_id"
                CREATE INDEX IF NOT EXISTS idx_memory_external
                    ON memory(external_id);

                -- Chat: "show last 20 conversations for user_1"
                CREATE INDEX IF NOT EXISTS idx_chat_user
                    ON chat_history(user_id, created_at);

                -- Connections: "which tools does user_1 have connected?"
                CREATE INDEX IF NOT EXISTS idx_connections_user
                    ON connections(user_id, tool);
            """)
            await db.commit()
            logger.info(f"Database initialized at {self.db_path}")

    # =========================================================================
    # USERS — Create and retrieve user profiles
    # =========================================================================

    async def create_user(self, user_id, name=None, email=None, settings=None):
        """
        Register a new user in the database.

        How it works:
            - Tries to INSERT a new row with the given user_id
            - If user_id already exists (PRIMARY KEY conflict), returns False
            - Settings is stored as JSON string, so you can put anything:
              {"theme": "dark", "notifications": true, "timezone": "UTC+6"}

        Args:
            user_id  (str): Unique identifier (e.g., "user_1", UUID, or email)
            name     (str): Display name (optional)
            email    (str): Email address (optional)
            settings (dict): Any user preferences as dict (optional, stored as JSON)

        Returns:
            True  → user was created successfully
            False → user_id already exists (no changes made)

        Example:
            created = await db.create_user("user_1", name="John", email="john@example.com")
            # created = True (first time)
            # created = False (if called again with same user_id)
        """
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
        """
        Fetch a single user by their ID.

        How it works:
            - Runs SELECT on users table with the given user_id
            - Parses the settings JSON string back into a Python dict
            - Returns None if user doesn't exist (no error thrown)

        Args:
            user_id (str): The user's unique identifier

        Returns:
            dict → {"id", "name", "email", "settings" (as dict), "created_at"}
            None → if user_id not found

        Example:
            user = await db.get_user("user_1")
            # user = {"id": "user_1", "name": "John", "settings": {"theme": "dark"}, ...}
            # user = None (if not found)
        """
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

    # =========================================================================
    # CONNECTIONS — Track which OAuth tools are linked
    # =========================================================================

    async def save_connection(self, connection_id, user_id, tool, composio_id=None, status="connected"):
        """
        Save or update a tool's OAuth connection status.

        How it works:
            - Uses UPSERT (INSERT ... ON CONFLICT DO UPDATE)
            - If connection_id already exists → updates status, composio_id, connected_at
            - If new → inserts a fresh row
            - connected_at is auto-set to current UTC time

        When is this called?
            - After user completes OAuth flow for Gmail/Slack/Outlook
            - When checking connection health (update status to "active"/"expired")
            - When user disconnects a tool (status = "disconnected")

        Args:
            connection_id (str): Unique ID for this connection (from Composio or generated)
            user_id       (str): Which user owns this connection
            tool          (str): "gmail", "slack", or "outlook"
            composio_id   (str): Composio's internal connection ID (optional)
            status        (str): "connected", "disconnected", "expired" (default: "connected")

        Example:
            await db.save_connection("conn_abc123", "user_1", "gmail",
                                     composio_id="comp_xyz", status="connected")
        """
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
        """
        Get ALL tool connections for a user.

        How it works:
            - Simple SELECT filtered by user_id
            - Returns list of dicts (one per connection)
            - Empty list if user has no connections

        Args:
            user_id (str): The user's ID

        Returns:
            list[dict] → [{"id", "user_id", "tool", "composio_id", "status", "connected_at"}, ...]

        Example:
            connections = await db.get_connections("user_1")
            # [{"id": "conn_1", "tool": "gmail", "status": "connected"}, ...]
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM connections WHERE user_id = ?", (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # =========================================================================
    # MEMORY (THE 2ND BRAIN) — Cache & retrieve data from all tools
    # =========================================================================

    async def cache_data(self, user_id, tool, data_type, content, external_id=None):
        """
        Store one item in the memory cache.

        This is the CORE caching method. Every piece of data from Composio
        (emails, slack messages, calendar events) goes through here.

        How it works:
            WITH external_id (e.g., Gmail message_id):
                - Uses UPSERT on UNIQUE(user_id, tool, external_id)
                - If same message was cached before → UPDATE content + cached_at
                - If new → INSERT fresh row
                - This prevents duplicate emails/messages in the cache

            WITHOUT external_id:
                - Simple INSERT (no dedup possible without an ID)
                - Used for data without a unique identifier

        Why UPSERT matters:
            User asks "show my emails" at 10am → 5 emails cached
            User asks "show my emails" at 11am → same 5 emails fetched
            Without UPSERT: 10 rows (duplicates!)
            With UPSERT: still 5 rows, but content updated to latest version

        Args:
            user_id     (str):  Which user's data this is
            tool        (str):  "gmail", "slack", "outlook" (which service)
            data_type   (str):  "email", "message", "event", "channel" (what kind of data)
            content     (dict): The actual data — stored as JSON in the content column
                                Example: {"subject": "Hello", "from": "boss@co.com", "body": "..."}
            external_id (str):  Unique ID from the external service (optional but recommended)
                                Gmail: message_id, Slack: message_ts, Outlook: message_id

        Example:
            # Cache a Gmail email
            await db.cache_data(
                user_id="user_1",
                tool="gmail",
                data_type="email",
                content={"subject": "Meeting", "from": "boss@co.com", "body": "Tomorrow at 3pm"},
                external_id="msg_abc123"
            )

            # Cache a Slack message
            await db.cache_data(
                user_id="user_1",
                tool="slack",
                data_type="message",
                content={"channel": "#general", "user": "john", "text": "Deploy done!"},
                external_id="1708123456.000100"
            )
        """
        content_json = json.dumps(content) if isinstance(content, dict) else content
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            if external_id:
                # UPSERT: insert new OR update existing on (user_id, tool, external_id) match
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
                # No external_id → plain INSERT (can't dedup without a unique key)
                await db.execute(
                    "INSERT INTO memory (user_id, tool, data_type, content, cached_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, tool, data_type, content_json, now)
                )
            await db.commit()

    async def cache_batch(self, user_id, tool, data_type, items, id_field="id"):
        """
        Cache MULTIPLE items at once — bulk version of cache_data().

        How it works:
            - Loops through each item (dict) and calls cache_data()
            - Extracts external_id from each item using id_field key
            - Each item gets its own UPSERT (safe, no duplicates)

        When is this called?
            - After fetching a list of emails from Gmail API
            - After fetching Slack channel messages
            - After fetching calendar events from Outlook
            Basically: whenever Composio returns a LIST of results.

        Args:
            user_id   (str):  Which user
            tool      (str):  "gmail", "slack", "outlook"
            data_type (str):  "email", "message", "event"
            items     (list): List of dicts — each dict is one item to cache
            id_field  (str):  Which key in each dict is the external_id
                              Default: "id" (most APIs use this)
                              Gmail might use "message_id", Slack uses "ts"

        Example:
            emails = [
                {"id": "msg_001", "subject": "Hello", "from": "a@b.com"},
                {"id": "msg_002", "subject": "Meeting", "from": "c@d.com"},
            ]
            await db.cache_batch("user_1", "gmail", "email", emails, id_field="id")
            # Both emails are now cached with dedup on external_id
        """
        for item in items:
            external_id = item.get(id_field)
            await self.cache_data(user_id, tool, data_type, item, external_id)

    async def search_memory(self, user_id, tool=None, data_type=None, query=None, limit=50):
        """
        Search the local cache (memory table).

        This is the FIRST stop in the SEARCH fallback chain:
            1. search_memory() ← YOU ARE HERE (fast, free, local)
            2. ChromaDB semantic search (if keyword search misses)
            3. Composio API call (last resort, slow, costly)

        How it works:
            - Builds a dynamic WHERE clause from provided filters
            - All filters are optional — combine any way you want
            - Text search uses LIKE on the JSON content column
            - Results sorted by cached_at DESC (newest first)

        Args:
            user_id   (str):          Required — always filter by user
            tool      (str or list):  Optional — single tool "gmail" OR multiple ["gmail", "slack"]
            data_type (str or list):  Optional — single type "email" OR multiple ["email", "message"]
            query     (str):          Optional — text to search inside JSON content (uses LIKE %query%)
            limit     (int):          Max results to return (default 50)

        Returns:
            list[dict] → each dict has: id, tool, data_type, external_id, content (as dict), cached_at
            Empty list if nothing found

        Example:
            # Get all Gmail emails for user
            results = await db.search_memory("user_1", tool="gmail")

            # Search across MULTIPLE tools at once (multi-agent queries)
            results = await db.search_memory("user_1", tool=["gmail", "slack"])

            # Search for "meeting" across ALL tools
            results = await db.search_memory("user_1", query="meeting")

            # Get only Slack messages mentioning "deploy"
            results = await db.search_memory("user_1", tool="slack", data_type="message", query="deploy")
        """
        # Build WHERE clause dynamically based on provided filters
        conditions = ["user_id = ?"]
        params = [user_id]

        if tool:
            # Support both single string and list of tools
            # "gmail" → tool = ?
            # ["gmail", "slack"] → tool IN (?, ?)
            if isinstance(tool, list):
                placeholders = ", ".join("?" for _ in tool)
                conditions.append(f"tool IN ({placeholders})")
                params.extend(tool)
            else:
                conditions.append("tool = ?")
                params.append(tool)
        if data_type:
            # Same pattern: support single or list
            if isinstance(data_type, list):
                placeholders = ", ".join("?" for _ in data_type)
                conditions.append(f"data_type IN ({placeholders})")
                params.extend(data_type)
            else:
                conditions.append("data_type = ?")
                params.append(data_type)
        if query:
            # LIKE search inside JSON content — simple but effective for keywords
            # For semantic/fuzzy search, use ChromaDB (vector_store.py)
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
        """
        Execute a RAW SQL query on the database (SELECT only).

        WHY THIS EXISTS:
            The SQL Generator (Groq LLM) creates dynamic SQLite queries
            from natural language. Example:
                User: "how many emails from john?"
                Groq generates: "SELECT COUNT(*) FROM memory
                                  WHERE tool='gmail'
                                  AND json_extract(content, '$.from') LIKE '%john%'"
            This method executes that generated SQL safely.

        SAFETY MEASURES:
            1. Must start with SELECT — no INSERT, UPDATE, DELETE
            2. Blocks dangerous keywords: DROP, ALTER, CREATE, PRAGMA, etc.
            3. Uses parameterized queries to prevent SQL injection
            4. If any check fails → raises ValueError (query is NOT executed)

        Args:
            sql    (str):  The SELECT query to execute
            params (list): Optional parameters for ? placeholders

        Returns:
            list[dict] → each row as a dictionary

        Raises:
            ValueError → if query is not SELECT or contains dangerous keywords

        Example:
            # Safe query — will execute
            results = await db.execute_sql(
                "SELECT * FROM memory WHERE user_id = ? AND content LIKE ?",
                ["user_1", "%meeting%"]
            )

            # Dangerous query — will raise ValueError
            await db.execute_sql("DROP TABLE memory")  # BLOCKED!
            await db.execute_sql("DELETE FROM memory")  # BLOCKED!
        """
        sql_stripped = sql.strip().upper()

        # SAFETY CHECK 1: Must be a SELECT query
        if not sql_stripped.startswith("SELECT"):
            raise ValueError("Only SELECT queries allowed")

        # SAFETY CHECK 2: Block all mutation/destruction keywords
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
        """
        Delete cached data from memory table.

        WHY THIS EXISTS:
            When a user performs an ACTION (send email, post message),
            the cached data becomes STALE. Old search results no longer
            reflect reality. So we wipe the relevant cache.

        When is this called?
            - User sends an email → invalidate gmail email cache
            - User posts in Slack → invalidate slack message cache
            - User creates calendar event → invalidate outlook event cache

        How it works:
            - Builds DELETE query based on filters
            - If only user_id → deletes ALL cached data for that user
            - If tool specified → only that tool's cache (e.g., just gmail)
            - If data_type specified → even more targeted (e.g., just gmail emails)

        Args:
            user_id   (str):         Required — which user's cache to clear
            tool      (str or list): Optional — clear one tool "gmail" or multiple ["gmail", "slack"]
            data_type (str or list): Optional — clear one type or multiple

        Example:
            # User sent an email → stale gmail cache
            await db.invalidate_cache("user_1", tool="gmail")

            # Clear both gmail and slack cache at once
            await db.invalidate_cache("user_1", tool=["gmail", "slack"])

            # User posted in Slack → stale slack messages
            await db.invalidate_cache("user_1", tool="slack", data_type="message")

            # Nuclear option: clear everything for user
            await db.invalidate_cache("user_1")
        """
        conditions = ["user_id = ?"]
        params = [user_id]

        if tool:
            if isinstance(tool, list):
                placeholders = ", ".join("?" for _ in tool)
                conditions.append(f"tool IN ({placeholders})")
                params.extend(tool)
            else:
                conditions.append("tool = ?")
                params.append(tool)
        if data_type:
            if isinstance(data_type, list):
                placeholders = ", ".join("?" for _ in data_type)
                conditions.append(f"data_type IN ({placeholders})")
                params.extend(data_type)
            else:
                conditions.append("data_type = ?")
                params.append(data_type)

        where = " AND ".join(conditions)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"DELETE FROM memory WHERE {where}", params)
            await db.commit()
            logger.info(f"Cache invalidated: user={user_id}, tool={tool}, type={data_type}")

    async def get_memory_count(self, user_id, tool=None):
        """
        Count how many items are cached for a user.

        Useful for:
            - Debugging: "does the cache have anything?"
            - Dashboard stats: "42 emails cached, 18 slack messages"
            - Deciding whether to hit Composio: count=0 → must fetch externally

        Args:
            user_id (str):         Required — which user
            tool    (str or list): Optional — count for one tool or multiple

        Returns:
            int → number of cached items

        Example:
            total = await db.get_memory_count("user_1")                         # 60
            gmail = await db.get_memory_count("user_1", tool="gmail")           # 42
            both  = await db.get_memory_count("user_1", tool=["gmail","slack"]) # 60
        """
        conditions = ["user_id = ?"]
        params = [user_id]
        if tool:
            if isinstance(tool, list):
                placeholders = ", ".join("?" for _ in tool)
                conditions.append(f"tool IN ({placeholders})")
                params.extend(tool)
            else:
                conditions.append("tool = ?")
                params.append(tool)

        where = " AND ".join(conditions)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM memory WHERE {where}", params
            ) as cursor:
                row = await cursor.fetchone()
                return row[0]

    # =========================================================================
    # CHAT HISTORY — Log every conversation for context & debugging
    # =========================================================================

    async def log_chat(self, user_id, query, response, route=None, agent=None, operations=None):
        """
        Save a user query and the system's response to chat_history.

        WHY THIS EXISTS:
            1. Context: "what did I ask before?" — enables follow-up queries
            2. Debugging: see which agent handled which query, what operations ran
            3. Analytics: which tools are used most, what types of queries are common
            4. Future: RAG over past conversations ("what did I discuss last week?")

        How it works:
            - Simple INSERT — chat history is append-only (never updated/deleted)
            - response and operations are stored as JSON strings
            - created_at is auto-set by SQLite

        Args:
            user_id    (str):  Which user asked
            query      (str):  The raw user query text (e.g., "show my unread emails")
            response   (dict): The system's response — any dict, stored as JSON
            route      (str):  Which route was taken (e.g., "GMAIL", "SLACK", "MULTI")
            agent      (str):  Which agent handled it (e.g., "gmail_agent", "supervisor")
            operations (list): What operations were performed (e.g., ["SEARCH", "CALCULATE"])

        Example:
            await db.log_chat(
                user_id="user_1",
                query="how many unread emails from boss?",
                response={"count": 3, "emails": [...]},
                route="GMAIL",
                agent="gmail_agent",
                operations=["SEARCH", "CALCULATE"]
            )
        """
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
        """
        Retrieve recent chat history for a user.

        How it works:
            - Fetches last N conversations, newest first
            - Parses response (JSON string → dict) and operations (JSON string → list)
            - Returns empty list if no history exists

        Args:
            user_id (str): Which user's history to fetch
            limit   (int): How many recent entries (default 20)

        Returns:
            list[dict] → each dict has: id, query, response (dict), route, agent, operations (list), created_at
            Ordered: newest first

        Example:
            history = await db.get_chat_history("user_1", limit=5)
            # [
            #   {"query": "show emails", "response": {...}, "agent": "gmail_agent", ...},
            #   {"query": "hello", "response": {...}, "agent": "general", ...},
            # ]
        """
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

    # =========================================================================
    # UTILITIES — Debugging & health checks
    # =========================================================================

    async def get_table_info(self):
        """
        Get row counts for all 4 tables.

        Useful for:
            - Health check: "is the DB working?"
            - Dashboard: "users: 5, memory: 342, chats: 89"
            - Debugging: "why is memory empty after caching?"

        Returns:
            dict → {"users": 1, "connections": 2, "memory": 42, "chat_history": 15}

        Example:
            info = await db.get_table_info()
            print(info)  # {"users": 1, "connections": 2, "memory": 42, "chat_history": 15}
        """
        async with aiosqlite.connect(self.db_path) as db:
            tables = {}
            for table in ["users", "connections", "memory", "chat_history"]:
                async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    row = await cursor.fetchone()
                    tables[table] = row[0]
            return tables


# =============================================================================
# SINGLETON ACCESSOR — Use this everywhere in the app
# =============================================================================

def get_database():
    """
    Get the singleton DatabaseManager instance.
    Created once on first call, reused everywhere after.

    Usage:
        from database import get_database
        db = get_database()
        await db.search_memory("user_1", tool="gmail")

    Why singleton?
        - One DB path, one instance, no confusion
        - Same pattern as get_brain_router() in llm_manager.py
        - All agents share the same DB connection config
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseManager()
    return _db_instance


# =============================================================================
# TEST BLOCK — Run with: python database/sqlite_manager.py
# =============================================================================
if __name__ == "__main__":
    import asyncio

    async def test():
        print("Testing DatabaseManager...")
        print("=" * 50)
        db = DatabaseManager(db_path="test_hassle_reducer.db")

        # 1. Init tables
        await db.init_db()
        print("[OK] Tables created")

        # 2. Create user
        created = await db.create_user("user_1", name="Test User", email="test@example.com")
        print(f"[OK] User created: {created}")

        # 3. Get user
        user = await db.get_user("user_1")
        print(f"[OK] User fetched: {user['name']}")

        # 4. Cache single email
        await db.cache_data("user_1", "gmail", "email", {
            "subject": "Meeting Tomorrow",
            "from": "boss@company.com",
            "body": "Don't forget the meeting at 3pm"
        }, external_id="msg_001")
        print("[OK] Email cached")

        # 5. Cache batch of slack messages
        await db.cache_batch("user_1", "slack", "message", [
            {"id": "slack_001", "channel": "#general", "text": "Hello team"},
            {"id": "slack_002", "channel": "#dev", "text": "Deploy is ready"},
        ])
        print("[OK] Batch cached (2 slack messages)")

        # 6. Search memory by tool
        results = await db.search_memory("user_1", tool="gmail")
        print(f"[OK] Gmail search: {len(results)} results")

        # 7. Search memory by text
        results = await db.search_memory("user_1", query="meeting")
        print(f"[OK] Text search 'meeting': {len(results)} results")

        # 7b. Search across MULTIPLE tools at once
        results = await db.search_memory("user_1", tool=["gmail", "slack"])
        print(f"[OK] Multi-tool search [gmail,slack]: {len(results)} results")

        # 7c. Count across multiple tools
        count = await db.get_memory_count("user_1", tool=["gmail", "slack"])
        print(f"[OK] Multi-tool count [gmail,slack]: {count}")

        # 8. Log chat
        await db.log_chat("user_1", "show my emails", {"result": "2 emails found"},
                          route="GMAIL", agent="gmail_agent", operations=["SEARCH"])
        print("[OK] Chat logged")

        # 9. Get chat history
        history = await db.get_chat_history("user_1")
        print(f"[OK] Chat history: {len(history)} entries")

        # 10. Table info
        info = await db.get_table_info()
        print(f"[OK] Table counts: {info}")

        # 11. Execute raw SQL
        results = await db.execute_sql(
            "SELECT * FROM memory WHERE user_id = ? AND content LIKE ?",
            ["user_1", "%meeting%"]
        )
        print(f"[OK] Raw SQL: {len(results)} results")

        # 12. Invalidate cache
        await db.invalidate_cache("user_1", tool="gmail")
        count = await db.get_memory_count("user_1", tool="gmail")
        print(f"[OK] After invalidate gmail: {count} gmail items")

        # Cleanup test DB
        import os
        os.remove("test_hassle_reducer.db")
        print("\n" + "=" * 50)
        print("[ALL TESTS PASSED]")

    asyncio.run(test())

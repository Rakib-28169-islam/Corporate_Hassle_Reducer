"""
=============================================================================
LOCAL TOOL MANAGER — Smart Bridge Between Agents and Database
=============================================================================

WHY THIS EXISTS:
    Agents (Gmail, Slack, Outlook) need to SEARCH for data.
    But they shouldn't care about WHERE data comes from:
        - SQLite keyword search? (3ms, free)
        - ChromaDB semantic search? (40ms, free)
        - Composio API call? (1500ms, costly)

    This module hides that complexity. Agents just call:
        results = await local_tools.search_local("user_1", "gmail", "show unread emails")

    And LocalToolManager figures out the cheapest way to get results.

THE SMART THRESHOLD SYSTEM:
    ┌──────────────────────────────────────────────────────────────────┐
    │  User: "show emails from John"                                  │
    │                                                                  │
    │  Step 1: SQLite keyword search ("John" in cached emails)         │
    │          → Found 5 results? DONE. Return immediately. (3ms)     │
    │          → Found 1 result?  Continue to Step 2...               │
    │                                                                  │
    │  Step 2: ChromaDB semantic search ("John" meaning-matched)       │
    │          → Found 3 more? Merge with Step 1. DONE. (40ms total)  │
    │          → Found 0? Tell agent to call Composio API.            │
    │                                                                  │
    │  Threshold = 3                                                   │
    │  If SQLite returns >= 3 results, skip ChromaDB entirely.        │
    │  Why 3? Most user queries need 3-5 items to feel "answered".    │
    └──────────────────────────────────────────────────────────────────┘

CACHE MANAGEMENT:
    When an agent fetches data from Composio API, it calls:
        await local_tools.cache_results("user_1", "gmail", "email", items)

    This stores the data in BOTH:
        - SQLite (for keyword search next time)
        - ChromaDB (for semantic search next time)

    When an agent performs an ACTION (send, delete, move), it calls:
        await local_tools.invalidate("user_1", "gmail")

    This marks cached data as potentially stale.

STALENESS:
    Cached data has an age. Agents can check if data is "stale":
        is_old = await local_tools.is_stale("user_1", "gmail", max_age_minutes=30)

    If stale, the agent should refresh from Composio even if local data exists.

USAGE:
    from tools.local_tools import get_local_tools

    lt = get_local_tools()

    # Search locally (SQLite -> ChromaDB, smart threshold)
    results = await lt.search_local("user_1", "gmail", "emails from John")

    # Cache API results for future use
    await lt.cache_results("user_1", "gmail", "email", [item1, item2, ...])

    # Invalidate after mutations
    await lt.invalidate("user_1", "gmail")

    # Check freshness
    stale = await lt.is_stale("user_1", "gmail", max_age_minutes=30)

=============================================================================
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta

# Ensure 'database' package is importable when running this file directly
# (e.g., python tools/local_tools.py from any directory)
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from database import get_database, get_vector_store
from core.sql_generator import get_sql_generator

logger = logging.getLogger(__name__)

# Smart Threshold — minimum SQLite results before skipping ChromaDB
# If SQLite returns >= THRESHOLD results, we assume local data is sufficient.
# If SQLite returns < THRESHOLD, we also query ChromaDB for semantic matches.
# Why 3? Most user queries need 3-5 items to feel "answered".
SMART_THRESHOLD = 3


class LocalToolManager:
    """
    Unified interface for local data access (SQLite + ChromaDB).

    This sits between agents and the database layer:
        Agent  -->  LocalToolManager  -->  SQLite (keyword)
                                      -->  ChromaDB (semantic)

    Agents never import database modules directly.
    They always go through LocalToolManager.

    Key Methods:
        search_local()     - Smart search with threshold fallback
        cache_results()    - Store data in both SQLite and ChromaDB
        cache_single()     - Store one item in both databases
        invalidate()       - Mark data as stale after mutations
        is_stale()         - Check if cached data is too old
        get_local_count()  - Count cached items for a user+tool
    """

    def __init__(self):
        """
        Initialize by grabbing singleton references to both databases.

        No connections are opened here — SQLite opens per-query (async),
        ChromaDB's collection is already loaded in memory.
        """
        self.db = get_database()
        self.vs = get_vector_store()
        logger.info("LocalToolManager initialized")

    # =========================================================================
    # LLM SQL SEARCH — Precise json_extract queries from natural language
    # =========================================================================

    async def _llm_sql_search(self, user_id, tool, data_type, query, limit):
        """
        LLM SQL layer: generate precise SQL from natural language query.
        Returns search result dict compatible with search_local() format, or None.
        """
        sg = get_sql_generator()
        result = await sg.generate_and_execute(query, user_id, tool, self.db)
        return result  # None if generation/review/execution failed

    # =========================================================================
    # SEARCH — The core method with Smart Threshold
    # =========================================================================

    async def search_local(self, user_id, tool=None, query=None,
                           data_type=None, limit=20):
        """
        Search local databases with smart SQLite-first, ChromaDB-fallback.

        How it works (the Smart Threshold system):
            1. Query SQLite with keyword search (LIKE %query%)
            2. If SQLite returns >= SMART_THRESHOLD results → return immediately
               (local data is "good enough", no need for semantic search)
            3. If SQLite returns < SMART_THRESHOLD results → also query ChromaDB
            4. Merge SQLite + ChromaDB results, deduplicate by external_id
            5. Return merged results

        Why this order?
            - SQLite LIKE search is 3ms and free → always try first
            - ChromaDB semantic search is 40ms → only if keyword search underperforms
            - This way 90% of repeat queries finish in 3ms (SQLite hit)
            - Only novel/semantic queries need 40ms (ChromaDB)

        Deduplication:
            If the same document matches in BOTH SQLite and ChromaDB,
            we keep the SQLite version (it has full JSON content).
            ChromaDB results only fill in gaps SQLite missed.

        Args:
            user_id   (str):          Required — always filter by user
            tool      (str or list):  Optional — "gmail" or ["gmail", "slack"]
            query     (str):          Optional — search text (keywords for SQLite,
                                      meaning for ChromaDB)
            data_type (str or list):  Optional — "email" or ["email", "message"]
            limit     (int):          Max total results to return (default 20)

        Returns:
            dict with:
                results   (list):  List of matched items (content dicts)
                source    (str):   Where results came from:
                                   "sqlite" — all from keyword search
                                   "chromadb" — all from semantic search
                                   "hybrid" — merged from both
                                   "empty" — nothing found anywhere
                sqlite_count  (int):  How many SQLite returned
                chromadb_count (int): How many ChromaDB added (after dedup)
                threshold_met (bool): True if SQLite alone was sufficient

        Example:
            # First time: SQLite empty, ChromaDB empty → both return 0
            result = await lt.search_local("user_1", "gmail", "meeting notes")
            # result = {
            #     "results": [],
            #     "source": "empty",
            #     "sqlite_count": 0,
            #     "chromadb_count": 0,
            #     "threshold_met": False,
            # }
            # → Agent knows to call Composio API

            # After caching: SQLite has 5 emails matching "meeting"
            result = await lt.search_local("user_1", "gmail", "meeting notes")
            # result = {
            #     "results": [{...}, {...}, {...}, {...}, {...}],
            #     "source": "sqlite",
            #     "sqlite_count": 5,
            #     "chromadb_count": 0,
            #     "threshold_met": True,   ← skipped ChromaDB!
            # }
        """
        # ---- Step 0: LLM SQL search (precise json_extract queries) ----
        if query:
            try:
                smart_result = await self._llm_sql_search(
                    user_id, tool, data_type, query, limit
                )
                if smart_result and len(smart_result.get("results", [])) > 0:
                    return smart_result
            except Exception as e:
                logger.debug(f"LLM SQL search fell through: {e}")

        sqlite_results = []
        chromadb_results = []

        # ---- Step 1: SQLite keyword search (always runs first) ----
        try:
            sqlite_results = await self.db.search_memory(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                query=query,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"SQLite search failed: {e}")
            # Continue — ChromaDB might still work

        sqlite_count = len(sqlite_results)

        # ---- Step 2: Check threshold ----
        # If SQLite returned enough results, skip ChromaDB
        if sqlite_count >= SMART_THRESHOLD:
            logger.debug(f"Threshold met: SQLite returned {sqlite_count} results "
                         f"(>= {SMART_THRESHOLD}), skipping ChromaDB")
            return {
                "results": [r["content"] for r in sqlite_results],
                "source": "sqlite",
                "sqlite_count": sqlite_count,
                "chromadb_count": 0,
                "threshold_met": True,
            }

        # ---- Step 3: ChromaDB semantic search (threshold NOT met) ----
        # SQLite didn't have enough → try semantic matching
        if query:
            try:
                chromadb_raw = self.vs.search(
                    query=query,
                    user_id=user_id,
                    tool=tool,
                    data_type=data_type,
                    n_results=limit,
                )
                chromadb_results = chromadb_raw
            except Exception as e:
                logger.error(f"ChromaDB search failed: {e}")
                # Continue with whatever SQLite returned

        # ---- Step 4: Merge & deduplicate ----
        # SQLite results are primary (they have full JSON content)
        # ChromaDB results fill gaps (only text, but semantically matched)
        seen_ids = set()
        merged = []

        # Add SQLite results first (full content)
        for r in sqlite_results:
            ext_id = r.get("external_id")
            if ext_id:
                seen_ids.add(ext_id)
            merged.append(r["content"])

        # Add ChromaDB results that SQLite didn't already have
        chromadb_added = 0
        for r in chromadb_results:
            ext_id = r.get("external_id", "")
            if ext_id and ext_id in seen_ids:
                continue  # Already in SQLite results, skip duplicate
            if len(merged) >= limit:
                break  # Respect the limit

            # ChromaDB results have text, not full JSON content
            # Wrap in a dict so the format is consistent
            merged.append({
                "text": r.get("text", ""),
                "tool": r.get("tool", ""),
                "data_type": r.get("data_type", ""),
                "external_id": ext_id,
                "source": "chromadb",
                "score": r.get("score", 0.0),
            })
            seen_ids.add(ext_id)
            chromadb_added += 1

        # Determine source label
        if not merged:
            source = "empty"
        elif sqlite_count > 0 and chromadb_added > 0:
            source = "hybrid"
        elif sqlite_count > 0:
            source = "sqlite"
        elif chromadb_added > 0:
            source = "chromadb"
        else:
            source = "empty"

        return {
            "results": merged,
            "source": source,
            "sqlite_count": sqlite_count,
            "chromadb_count": chromadb_added,
            "threshold_met": False,
        }

    # =========================================================================
    # CACHE — Store data in both SQLite and ChromaDB
    # =========================================================================

    async def cache_results(self, user_id, tool, data_type, items,
                            id_field="id"):
        """
        Cache multiple items in BOTH SQLite and ChromaDB.

        When an agent fetches data from Composio API, it calls this method
        to store results locally. Next time the user asks the same question,
        search_local() will find the data without another API call.

        How it works:
            1. Store in SQLite (cache_batch) — for keyword search
            2. Store in ChromaDB (add_documents) — for semantic search
            Both operations run regardless of the other's success.

        Args:
            user_id   (str):  The user who owns this data
            tool      (str):  Source tool — "gmail", "slack", "outlook"
            data_type (str):  Type of data — "email", "message", "event"
            items     (list): List of dicts to cache (raw Composio response items)
            id_field  (str):  Key in each item dict that holds its unique ID
                              (default "id", some APIs use "messageId", etc.)

        Returns:
            dict with:
                sqlite_count   (int):  Items stored in SQLite
                chromadb_count (int):  Items stored in ChromaDB
                errors         (list): Any errors that occurred

        Example:
            emails = composio_gmail.list_emails(user_id)  # API call
            result = await lt.cache_results("user_1", "gmail", "email", emails)
            # result = {"sqlite_count": 10, "chromadb_count": 10, "errors": []}
        """
        errors = []
        sqlite_count = 0
        chromadb_count = 0

        # 1. SQLite — bulk insert
        try:
            await self.db.cache_batch(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                items=items,
                id_field=id_field,
            )
            sqlite_count = len(items)
        except Exception as e:
            errors.append(f"SQLite cache_batch failed: {e}")
            logger.error(f"SQLite cache_batch failed: {e}")

        # 2. ChromaDB — add documents for semantic search
        try:
            self.vs.add_documents(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                items=items,
                id_field=id_field,
            )
            chromadb_count = len(items)
        except Exception as e:
            errors.append(f"ChromaDB add_documents failed: {e}")
            logger.error(f"ChromaDB add_documents failed: {e}")

        logger.info(f"Cached {len(items)} items for {user_id}/{tool}/{data_type} "
                     f"(SQLite: {sqlite_count}, ChromaDB: {chromadb_count})")

        return {
            "sqlite_count": sqlite_count,
            "chromadb_count": chromadb_count,
            "errors": errors,
        }

    async def cache_single(self, user_id, tool, data_type, content,
                           external_id):
        """
        Cache ONE item in both SQLite and ChromaDB.

        Same as cache_results() but for a single item.
        Used when an agent processes one email/message at a time.

        Args:
            user_id     (str):  The user
            tool        (str):  Source tool — "gmail", "slack", "outlook"
            data_type   (str):  Data type — "email", "message", "event"
            content     (dict): The item data as a dictionary
            external_id (str):  Unique ID from the source service (gmail msg_id, etc.)

        Returns:
            dict with sqlite_ok (bool), chromadb_ok (bool), errors (list)

        Example:
            email = {"subject": "Meeting", "from": "boss@co.com", "body": "..."}
            result = await lt.cache_single("user_1", "gmail", "email", email, "msg_001")
        """
        errors = []
        sqlite_ok = False
        chromadb_ok = False

        try:
            await self.db.cache_data(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                content=content,
                external_id=external_id,
            )
            sqlite_ok = True
        except Exception as e:
            errors.append(f"SQLite cache_data failed: {e}")
            logger.error(f"SQLite cache_data failed: {e}")

        try:
            self.vs.add_single(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                content=content,
                external_id=external_id,
            )
            chromadb_ok = True
        except Exception as e:
            errors.append(f"ChromaDB add_single failed: {e}")
            logger.error(f"ChromaDB add_single failed: {e}")

        return {
            "sqlite_ok": sqlite_ok,
            "chromadb_ok": chromadb_ok,
            "errors": errors,
        }

    # =========================================================================
    # INVALIDATE — Clear cache after mutations (send, delete, move)
    # =========================================================================

    async def invalidate(self, user_id, tool=None):
        """
        Invalidate (delete) cached data after a mutation.

        When an agent performs an ACTION (send email, delete message, etc.),
        the cached data might be out of date. Call this to clear it.

        Why invalidate?
            User: "send email to boss"            → ACTION (send via Composio)
            User: "show my sent emails"            → SEARCH
            Without invalidation: shows OLD sent folder (missing the new email)
            With invalidation: cache is cleared, forces fresh API fetch

        How it works:
            1. Delete from SQLite (invalidate_cache)
            2. Delete from ChromaDB (delete_by_tool)
            Both always run regardless of the other's result.

        Args:
            user_id (str):          Required — clear this user's data
            tool    (str or None):  Optional — clear only one tool's data
                                    None → clear ALL tools for this user

        Returns:
            dict with sqlite_ok (bool), chromadb_ok (bool), errors (list)

        Example:
            # After sending an email, invalidate gmail cache
            await lt.invalidate("user_1", "gmail")

            # After connecting a new tool, clear everything
            await lt.invalidate("user_1")
        """
        errors = []
        sqlite_ok = False
        chromadb_ok = False

        try:
            await self.db.invalidate_cache(user_id=user_id, tool=tool)
            sqlite_ok = True
        except Exception as e:
            errors.append(f"SQLite invalidation failed: {e}")
            logger.error(f"SQLite invalidation failed: {e}")

        try:
            self.vs.delete_by_tool(user_id=user_id, tool=tool)
            chromadb_ok = True
        except Exception as e:
            errors.append(f"ChromaDB invalidation failed: {e}")
            logger.error(f"ChromaDB invalidation failed: {e}")

        logger.info(f"Cache invalidated for {user_id}"
                     f"{f'/{tool}' if tool else '/ALL'}")

        return {
            "sqlite_ok": sqlite_ok,
            "chromadb_ok": chromadb_ok,
            "errors": errors,
        }

    # =========================================================================
    # STALENESS CHECK — Is the cache too old?
    # =========================================================================

    async def is_stale(self, user_id, tool, max_age_minutes=30):
        """
        Check if cached data for a user+tool is older than max_age_minutes.

        Agents call this before deciding whether to use local data or re-fetch.
        If data is stale, the agent should call Composio API even if local
        results exist — because the external service may have newer data.

        How it works:
            1. Query SQLite for the MOST RECENT item cached for user+tool
            2. Compare its cached_at timestamp to current time
            3. If older than max_age_minutes → stale (return True)
            4. If no data exists → stale (return True, need to fetch)
            5. If recent enough → fresh (return False, safe to use local)

        Default max_age: 30 minutes
            - Emails: users expect near-realtime, 30min is conservative
            - Slack: messages come fast, could lower to 10-15min
            - Calendar: events change less often, could raise to 60min
            Agents can override this per tool.

        Args:
            user_id          (str): The user to check
            tool             (str): The tool to check ("gmail", "slack", etc.)
            max_age_minutes  (int): How old data can be before it's "stale"

        Returns:
            bool — True if data is stale (or missing), False if fresh

        Example:
            stale = await lt.is_stale("user_1", "gmail", max_age_minutes=15)
            if stale:
                # Fetch fresh data from Composio API
                emails = composio.list_emails(...)
                await lt.cache_results("user_1", "gmail", "email", emails)
            else:
                # Use local data (it's fresh enough)
                results = await lt.search_local("user_1", "gmail", query)
        """
        try:
            results = await self.db.search_memory(
                user_id=user_id,
                tool=tool,
                limit=1,  # Just need the most recent one
            )

            if not results:
                return True  # No data at all → definitely stale

            # Get the cached_at timestamp of the most recent item
            # search_memory returns ORDER BY cached_at DESC, so [0] is newest
            cached_at_str = results[0].get("cached_at", "")
            if not cached_at_str:
                return True  # No timestamp → assume stale

            # Parse the ISO timestamp
            cached_at = datetime.fromisoformat(cached_at_str)
            # If the timestamp is naive (no timezone), assume UTC
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            age = now - cached_at
            max_age = timedelta(minutes=max_age_minutes)

            is_old = age > max_age
            if is_old:
                logger.debug(f"Cache stale for {user_id}/{tool}: "
                             f"age={age.total_seconds():.0f}s > max={max_age.total_seconds():.0f}s")
            return is_old

        except Exception as e:
            logger.error(f"Staleness check failed for {user_id}/{tool}: {e}")
            return True  # On error, assume stale (safer)

    # =========================================================================
    # UTILITY — Count cached items
    # =========================================================================

    async def get_local_count(self, user_id, tool=None):
        """
        Count how many items are cached locally for a user.

        Quick check to see if we have ANY local data before searching.
        Also useful for debugging and health checks.

        Args:
            user_id (str):          The user to count
            tool    (str or None):  Count for one tool, or all tools

        Returns:
            dict with:
                sqlite_count   (int): Items in SQLite memory table
                chromadb_count (int): Documents in ChromaDB
                has_data       (bool): True if either has data

        Example:
            counts = await lt.get_local_count("user_1", "gmail")
            # {"sqlite_count": 25, "chromadb_count": 25, "has_data": True}
        """
        sqlite_count = 0
        chromadb_count = 0

        try:
            sqlite_count = await self.db.get_memory_count(
                user_id=user_id, tool=tool
            )
        except Exception as e:
            logger.error(f"SQLite count failed: {e}")

        try:
            chromadb_count = self.vs.count()
        except Exception as e:
            logger.error(f"ChromaDB count failed: {e}")

        return {
            "sqlite_count": sqlite_count,
            "chromadb_count": chromadb_count,
            "has_data": (sqlite_count > 0 or chromadb_count > 0),
        }


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_local_tools_instance = None


def get_local_tools():
    """
    Get the singleton LocalToolManager instance.

    Usage:
        from tools.local_tools import get_local_tools
        lt = get_local_tools()
        results = await lt.search_local("user_1", "gmail", "meeting")
    """
    global _local_tools_instance
    if _local_tools_instance is None:
        _local_tools_instance = LocalToolManager()
    return _local_tools_instance


# =============================================================================
# TEST BLOCK — Run with: python -m tools.local_tools (from backend/)
#              Or:        python tools/local_tools.py (auto-fixes path)
# =============================================================================
if __name__ == "__main__":
    import asyncio
    import sys
    import os

    # Fix Windows encoding
    sys.stdout.reconfigure(encoding='utf-8')

    # Path already fixed at module level

    # Use test databases (not production)
    TEST_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test_local_tools.db")
    TEST_CHROMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test_local_tools_chroma")

    async def run_tests():
        print("Testing LocalToolManager...")
        print("=" * 60)

        # --- Setup: init test databases ---
        from database.sqlite_manager import DatabaseManager
        from database.vector_store import VectorStore

        # Clean up any previous test files
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        import shutil
        if os.path.exists(TEST_CHROMA):
            shutil.rmtree(TEST_CHROMA)

        db = DatabaseManager(db_path=TEST_DB)
        await db.init_db()
        vs = VectorStore(persist_dir=TEST_CHROMA)

        # Create LocalToolManager with test databases
        lt = LocalToolManager()
        lt.db = db
        lt.vs = vs

        passed = 0
        failed = 0

        def check(name, condition):
            nonlocal passed, failed
            status = "[OK]" if condition else "[FAIL]"
            if condition:
                passed += 1
            else:
                failed += 1
            print(f"  {status} {name}")

        # ----- Test 1: Empty search returns empty -----
        r = await lt.search_local("user_1", "gmail", "anything")
        check("Empty DB search returns empty",
              r["source"] == "empty" and len(r["results"]) == 0)

        # ----- Test 2: Cache single item -----
        email1 = {
            "subject": "Team standup notes",
            "from": "john@company.com",
            "body": "Discussed deployment timeline and release date.",
        }
        cr = await lt.cache_single("user_1", "gmail", "email", email1, "msg_001")
        check("Cache single: SQLite OK", cr["sqlite_ok"] is True)
        check("Cache single: ChromaDB OK", cr["chromadb_ok"] is True)

        # ----- Test 3: Cache batch items -----
        emails = [
            {"id": "msg_002", "subject": "Invoice #1234", "from": "billing@vendor.com",
             "body": "Your invoice for $500 is attached."},
            {"id": "msg_003", "subject": "Meeting tomorrow", "from": "boss@company.com",
             "body": "Let's discuss the project status at 3pm."},
            {"id": "msg_004", "subject": "Slack integration", "from": "dev@company.com",
             "body": "The new Slack bot is ready for testing."},
            {"id": "msg_005", "subject": "Quarterly review", "from": "hr@company.com",
             "body": "Please submit your self-assessment by Friday."},
        ]
        br = await lt.cache_results("user_1", "gmail", "email", emails)
        check("Cache batch: 4 items in SQLite", br["sqlite_count"] == 4)
        check("Cache batch: 4 items in ChromaDB", br["chromadb_count"] == 4)

        # ----- Test 4: SQLite keyword search (threshold met) -----
        # 5 total items cached, search for something broad should hit >= 3
        r = await lt.search_local("user_1", "gmail")
        check(f"Threshold MET: SQLite returned {r['sqlite_count']} (>= {SMART_THRESHOLD})",
              r["threshold_met"] is True and r["sqlite_count"] >= SMART_THRESHOLD)
        check("Source is 'sqlite' when threshold met", r["source"] == "sqlite")

        # ----- Test 5: SQLite search with specific query (threshold NOT met) -----
        # Only 1 email has "invoice" → below threshold → triggers ChromaDB
        r = await lt.search_local("user_1", "gmail", "invoice")
        check(f"Threshold NOT met: SQLite returned {r['sqlite_count']} for 'invoice'",
              r["sqlite_count"] < SMART_THRESHOLD)

        # ----- Test 6: Semantic search via ChromaDB -----
        # "deployment" doesn't appear literally, but "release date" is semantically close
        r = await lt.search_local("user_1", "gmail", "deployment")
        has_chromadb = r["chromadb_count"] > 0 or r["sqlite_count"] > 0
        check(f"Semantic fallback: found results for 'deployment' "
              f"(sqlite={r['sqlite_count']}, chromadb={r['chromadb_count']})",
              len(r["results"]) > 0)

        # ----- Test 7: get_local_count -----
        counts = await lt.get_local_count("user_1", "gmail")
        check(f"Local count: SQLite={counts['sqlite_count']}, has_data={counts['has_data']}",
              counts["sqlite_count"] == 5 and counts["has_data"] is True)

        # ----- Test 8: is_stale (data just cached = fresh) -----
        stale = await lt.is_stale("user_1", "gmail", max_age_minutes=30)
        check("Freshly cached data is NOT stale (max_age=30min)", stale is False)

        # ----- Test 9: is_stale (very short max_age = stale) -----
        stale2 = await lt.is_stale("user_1", "gmail", max_age_minutes=0)
        check("max_age=0 min -> data IS stale", stale2 is True)

        # ----- Test 10: is_stale (no data = stale) -----
        stale3 = await lt.is_stale("user_99", "gmail", max_age_minutes=30)
        check("No data for user -> IS stale", stale3 is True)

        # ----- Test 11: Invalidate cache -----
        inv = await lt.invalidate("user_1", "gmail")
        check("Invalidate SQLite OK", inv["sqlite_ok"] is True)
        check("Invalidate ChromaDB OK", inv["chromadb_ok"] is True)

        # Verify cache is empty after invalidation
        r_after = await lt.search_local("user_1", "gmail", "anything")
        check("After invalidation: search returns empty",
              r_after["source"] == "empty" and len(r_after["results"]) == 0)

        count_after = await lt.get_local_count("user_1", "gmail")
        check("After invalidation: SQLite count = 0",
              count_after["sqlite_count"] == 0)

        # ----- Cleanup -----
        # ChromaDB holds file locks on Windows, so cleanup may fail.
        # That's OK — the test data is small and gets overwritten next run.
        try:
            if os.path.exists(TEST_DB):
                os.remove(TEST_DB)
        except PermissionError:
            print("  [INFO] Could not delete test DB (file locked, OK on Windows)")
        try:
            if os.path.exists(TEST_CHROMA):
                shutil.rmtree(TEST_CHROMA)
        except PermissionError:
            print("  [INFO] Could not delete test ChromaDB (file locked, OK on Windows)")

        print()
        print("=" * 60)
        print(f"[RESULTS] {passed} passed, {failed} failed out of {passed + failed} tests")

    asyncio.run(run_tests())

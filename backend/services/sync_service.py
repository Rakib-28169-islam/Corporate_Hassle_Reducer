"""
=============================================================================
SYNC SERVICE — Cache Invalidation & Staleness Cleanup
=============================================================================

WHY THIS EXISTS:
    The local cache (SQLite + ChromaDB) stores copies of external data
    (emails, Slack messages, calendar events). Over time, this data goes stale:
        - User received new emails since the last cache
        - A Slack message was edited or deleted
        - A calendar event was rescheduled

    Without cleanup, users would see outdated information forever.

    This service handles TWO types of cache freshness:

    1. REACTIVE INVALIDATION (immediate)
       After an agent performs an ACTION (send, delete, move), the cache
       for that tool is immediately invalidated. The NEXT search will
       force a fresh API call.

       Example flow:
           User: "send email to boss"       -> ACTION (send via Composio)
           SyncService: invalidate("gmail")  -> clears cached gmail data
           User: "show my sent emails"       -> SEARCH (cache empty, fetches fresh)

    2. PERIODIC STALENESS CLEANUP (background)
       Even without user actions, data gets stale over time.
       A background task runs every N minutes and clears old data.

       Example:
           - Every 30 min: clear Gmail data older than 30 min
           - Every 60 min: clear Calendar data older than 60 min
           - User's next search auto-refreshes from API

HOW IT CONNECTS:
    ┌─────────────────────────────────────────────────────────┐
    │  Agent performs ACTION                                   │
    │      |                                                   │
    │  SyncService.on_action_complete(user_id, tool)          │
    │      |                                                   │
    │  LocalToolManager.invalidate(user_id, tool)             │
    │      |                                                   │
    │  SQLite: DELETE FROM memory WHERE user_id=? AND tool=?  │
    │  ChromaDB: delete_by_tool(user_id, tool)                │
    └─────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────┐
    │  Background loop (every 30 min)                          │
    │      |                                                   │
    │  SyncService.cleanup_stale()                             │
    │      |                                                   │
    │  For each user+tool: check is_stale()                   │
    │  If stale -> invalidate(user_id, tool)                  │
    └─────────────────────────────────────────────────────────┘

USAGE:
    from services.sync_service import get_sync_service

    sync = get_sync_service()

    # Reactive: after an agent sends an email
    await sync.on_action_complete("user_1", "gmail", "SEND_EMAIL")

    # Manual: force-refresh a tool's cache
    await sync.force_refresh("user_1", "gmail")

    # Background: start the periodic cleanup loop
    await sync.start_background_cleanup()

=============================================================================
"""

import os
import sys
import logging
import asyncio
from datetime import datetime, timezone

# Ensure imports work when running this file directly
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from tools.local_tools import get_local_tools

logger = logging.getLogger(__name__)


# =============================================================================
# DEFAULT STALENESS SETTINGS — How old data can be per tool
# =============================================================================
# Each tool has a different "freshness window" based on how fast data changes.
# Emails: new ones arrive frequently -> 30 min window
# Slack: messages come very fast -> 15 min window
# Calendar: events change less often -> 60 min window
# Default: anything not listed -> 30 min

DEFAULT_MAX_AGE = {
    "gmail": 30,       # 30 minutes
    "slack": 15,       # 15 minutes (messages are real-time)
    "outlook": 30,     # 30 minutes
    "calendar": 60,    # 60 minutes (events change less)
}

# How often the background cleanup runs (minutes)
CLEANUP_INTERVAL_MINUTES = 30


class SyncService:
    """
    Manages cache freshness through reactive invalidation and periodic cleanup.

    Two modes of operation:
        1. Reactive: Agent calls on_action_complete() after mutations
        2. Periodic: Background loop calls cleanup_stale() every N minutes

    This service does NOT fetch new data — it only CLEARS stale data.
    Agents are responsible for fetching fresh data on the next search.
    """

    def __init__(self):
        """
        Initialize SyncService with a reference to LocalToolManager.

        The LocalToolManager handles the actual SQLite + ChromaDB operations.
        SyncService just decides WHEN to invalidate.
        """
        self.local_tools = get_local_tools()
        self._cleanup_task = None  # asyncio background task handle
        self._running = False
        logger.info("SyncService initialized")

    # =========================================================================
    # REACTIVE INVALIDATION — Called by agents after mutations
    # =========================================================================

    async def on_action_complete(self, user_id, tool, action_type=None):
        """
        Called by an agent AFTER performing a mutation (send, delete, move, etc.).

        This immediately invalidates the cache for that user+tool,
        so the next search will fetch fresh data from the API.

        How it works:
            1. Invalidate SQLite cache for user+tool
            2. Invalidate ChromaDB vectors for user+tool
            3. Log the invalidation for debugging

        Why not invalidate ALL tools?
            If user sends a Gmail, only Gmail cache is stale.
            Slack and Calendar data is still valid. Invalidating
            everything would cause unnecessary API calls.

        Args:
            user_id     (str):          The user who performed the action
            tool        (str):          The tool that was used ("gmail", "slack", etc.)
            action_type (str, optional): What action was performed
                                         ("SEND_EMAIL", "DELETE_MESSAGE", etc.)
                                         Used for logging, not for logic.

        Returns:
            dict with: invalidated (bool), tool (str), action (str)

        Example:
            # In GmailAgent._do_action():
            result = await composio.send_email(...)
            await sync_service.on_action_complete("user_1", "gmail", "SEND_EMAIL")
        """
        logger.info(f"Reactive invalidation: {user_id}/{tool} "
                     f"(action: {action_type or 'unknown'})")

        result = await self.local_tools.invalidate(user_id, tool)

        return {
            "invalidated": result["sqlite_ok"] and result["chromadb_ok"],
            "tool": tool,
            "action": action_type or "unknown",
            "errors": result.get("errors", []),
        }

    # =========================================================================
    # FORCE REFRESH — Manual full invalidation
    # =========================================================================

    async def force_refresh(self, user_id, tool=None):
        """
        Force-clear all cached data for a user (or one specific tool).

        Use cases:
            - User reconnects a tool (OAuth re-auth)
            - User reports stale data ("I see old emails")
            - Admin debugging

        Args:
            user_id (str):          The user to refresh
            tool    (str, optional): Specific tool to clear.
                                     None -> clear ALL tools.

        Returns:
            dict with: refreshed (bool), tool (str), errors (list)

        Example:
            # Clear everything for user
            await sync.force_refresh("user_1")

            # Clear only Gmail
            await sync.force_refresh("user_1", "gmail")
        """
        logger.info(f"Force refresh: {user_id}/{tool or 'ALL'}")

        result = await self.local_tools.invalidate(user_id, tool)

        return {
            "refreshed": result["sqlite_ok"] and result["chromadb_ok"],
            "tool": tool or "ALL",
            "errors": result.get("errors", []),
        }

    # =========================================================================
    # STALENESS CHECK — Is data too old for a specific user+tool?
    # =========================================================================

    async def check_staleness(self, user_id, tool):
        """
        Check if a specific user+tool cache is stale.

        Uses the per-tool max_age settings from DEFAULT_MAX_AGE.
        Agents can call this before deciding whether to use cached data
        or fetch fresh from the API.

        Args:
            user_id (str): The user to check
            tool    (str): The tool to check

        Returns:
            dict with:
                stale         (bool): True if data is too old or missing
                tool          (str):  The tool checked
                max_age_min   (int):  The max age setting used

        Example:
            status = await sync.check_staleness("user_1", "gmail")
            if status["stale"]:
                # Fetch fresh from Composio
                ...
        """
        max_age = DEFAULT_MAX_AGE.get(tool, 30)
        is_stale = await self.local_tools.is_stale(user_id, tool, max_age)

        return {
            "stale": is_stale,
            "tool": tool,
            "max_age_min": max_age,
        }

    # =========================================================================
    # PERIODIC CLEANUP — Background loop
    # =========================================================================

    async def cleanup_stale(self, user_ids=None):
        """
        Check all tracked users and invalidate stale caches.

        This is the periodic cleanup method — called by the background loop
        or manually for maintenance.

        How it works:
            1. Get list of users to check (from SQLite memory table)
            2. For each user, check each tool's staleness
            3. If stale -> invalidate that user+tool cache
            4. Return summary of what was cleaned

        Args:
            user_ids (list, optional): Specific users to check.
                                       None -> check all users with cached data.

        Returns:
            dict with:
                checked   (int):  Total user+tool combinations checked
                cleaned   (int):  How many were stale and invalidated
                details   (list): Per-user+tool results

        Example:
            result = await sync.cleanup_stale()
            # result = {
            #     "checked": 6,
            #     "cleaned": 2,
            #     "details": [
            #         {"user": "user_1", "tool": "gmail", "stale": False},
            #         {"user": "user_1", "tool": "slack", "stale": True, "cleaned": True},
            #         ...
            #     ]
            # }
        """
        db = self.local_tools.db
        checked = 0
        cleaned = 0
        details = []

        tools_to_check = list(DEFAULT_MAX_AGE.keys())

        # If no specific users given, query distinct users from memory table
        if not user_ids:
            try:
                rows = await db.execute_sql(
                    "SELECT DISTINCT user_id FROM memory"
                )
                user_ids = [r["user_id"] for r in rows] if rows else []
            except Exception as e:
                logger.error(f"Could not get user list for cleanup: {e}")
                return {"checked": 0, "cleaned": 0, "details": []}

        for uid in user_ids:
            for tool in tools_to_check:
                checked += 1
                max_age = DEFAULT_MAX_AGE.get(tool, 30)
                is_stale = await self.local_tools.is_stale(uid, tool, max_age)

                detail = {
                    "user": uid,
                    "tool": tool,
                    "stale": is_stale,
                }

                if is_stale:
                    # Check if there's actually data to clean
                    count = await self.local_tools.get_local_count(uid, tool)
                    if count["has_data"]:
                        await self.local_tools.invalidate(uid, tool)
                        detail["cleaned"] = True
                        cleaned += 1
                    else:
                        detail["cleaned"] = False  # Nothing to clean

                details.append(detail)

        logger.info(f"Staleness cleanup: checked {checked}, cleaned {cleaned}")
        return {
            "checked": checked,
            "cleaned": cleaned,
            "details": details,
        }

    # =========================================================================
    # BACKGROUND LOOP — Runs periodically in the event loop
    # =========================================================================

    async def start_background_cleanup(self, interval_minutes=None):
        """
        Start a background asyncio task that periodically cleans stale caches.

        This should be called ONCE during FastAPI startup:
            @app.on_event("startup")
            async def startup():
                sync = get_sync_service()
                await sync.start_background_cleanup()

        The loop runs forever (until stop_background_cleanup is called).
        It sleeps for interval_minutes between each run.

        Args:
            interval_minutes (int, optional): Minutes between cleanup runs.
                                              Default: CLEANUP_INTERVAL_MINUTES (30)

        Example:
            await sync.start_background_cleanup(interval_minutes=15)
        """
        if self._running:
            logger.warning("Background cleanup already running")
            return

        interval = interval_minutes or CLEANUP_INTERVAL_MINUTES
        self._running = True

        async def _loop():
            logger.info(f"Background cleanup started (every {interval} min)")
            while self._running:
                await asyncio.sleep(interval * 60)
                try:
                    result = await self.cleanup_stale()
                    logger.info(f"Background cleanup done: "
                                f"{result['cleaned']} caches cleared")
                except Exception as e:
                    logger.error(f"Background cleanup error: {e}")

        self._cleanup_task = asyncio.create_task(_loop())

    async def stop_background_cleanup(self):
        """
        Stop the background cleanup loop.

        Called during FastAPI shutdown:
            @app.on_event("shutdown")
            async def shutdown():
                sync = get_sync_service()
                await sync.stop_background_cleanup()
        """
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Background cleanup stopped")


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_sync_instance = None


def get_sync_service():
    """
    Get the singleton SyncService instance.

    Usage:
        from services.sync_service import get_sync_service
        sync = get_sync_service()
        await sync.on_action_complete("user_1", "gmail", "SEND_EMAIL")
    """
    global _sync_instance
    if _sync_instance is None:
        _sync_instance = SyncService()
    return _sync_instance


# =============================================================================
# TEST BLOCK — Run with: python services/sync_service.py
# =============================================================================
if __name__ == "__main__":
    import shutil

    sys.stdout.reconfigure(encoding='utf-8')

    TEST_DB = os.path.join(_backend_dir, "test_sync.db")
    TEST_CHROMA = os.path.join(_backend_dir, "test_sync_chroma")

    async def run_tests():
        print("Testing SyncService...")
        print("=" * 60)

        from database.sqlite_manager import DatabaseManager
        from database.vector_store import VectorStore
        from tools.local_tools import LocalToolManager

        # Clean previous test files
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        try:
            if os.path.exists(TEST_CHROMA):
                shutil.rmtree(TEST_CHROMA)
        except PermissionError:
            pass

        # Setup test databases
        db = DatabaseManager(db_path=TEST_DB)
        await db.init_db()
        vs = VectorStore(persist_dir=TEST_CHROMA)

        lt = LocalToolManager()
        lt.db = db
        lt.vs = vs

        sync = SyncService()
        sync.local_tools = lt

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

        # ----- Seed test data -----
        emails = [
            {"id": "msg_001", "subject": "Invoice A", "body": "Amount: $100"},
            {"id": "msg_002", "subject": "Invoice B", "body": "Amount: $200"},
            {"id": "msg_003", "subject": "Meeting", "body": "Tomorrow at 3pm"},
            {"id": "msg_004", "subject": "Report", "body": "Q4 numbers"},
        ]
        await lt.cache_results("user_1", "gmail", "email", emails)

        slack_msgs = [
            {"id": "slack_001", "text": "Deploy at 5pm", "channel": "#dev"},
            {"id": "slack_002", "text": "Bug in login", "channel": "#bugs"},
        ]
        await lt.cache_results("user_1", "slack", "message", slack_msgs)

        # ----- Test 1: Check staleness (fresh data) -----
        status = await sync.check_staleness("user_1", "gmail")
        check("Fresh data is NOT stale", status["stale"] is False)
        check("Staleness check returns tool name", status["tool"] == "gmail")
        check("Staleness check returns max_age", status["max_age_min"] == 30)

        # ----- Test 2: Reactive invalidation -----
        counts_before = await lt.get_local_count("user_1", "gmail")
        check("Gmail has data before invalidation",
              counts_before["sqlite_count"] == 4)

        inv_result = await sync.on_action_complete(
            "user_1", "gmail", "SEND_EMAIL"
        )
        check("Reactive invalidation succeeded",
              inv_result["invalidated"] is True)
        check("Invalidation returns action type",
              inv_result["action"] == "SEND_EMAIL")

        counts_after = await lt.get_local_count("user_1", "gmail")
        check("Gmail cache empty after invalidation",
              counts_after["sqlite_count"] == 0)

        # ----- Test 3: Slack data NOT affected by Gmail invalidation -----
        slack_counts = await lt.get_local_count("user_1", "slack")
        check("Slack data NOT affected by Gmail invalidation",
              slack_counts["sqlite_count"] == 2)

        # ----- Test 4: Force refresh (all tools) -----
        refresh = await sync.force_refresh("user_1")
        check("Force refresh clears all tools",
              refresh["refreshed"] is True)

        slack_after = await lt.get_local_count("user_1", "slack")
        check("Slack also cleared by force_refresh(all)",
              slack_after["sqlite_count"] == 0)

        # ----- Test 5: Cleanup stale (no data = nothing to clean) -----
        cleanup = await sync.cleanup_stale(user_ids=["user_1"])
        check("Cleanup with empty cache: cleaned=0",
              cleanup["cleaned"] == 0)

        # ----- Test 6: Cleanup stale (with data) -----
        await lt.cache_results("user_1", "gmail", "email", emails)
        original_max_age = DEFAULT_MAX_AGE.get("gmail", 30)
        DEFAULT_MAX_AGE["gmail"] = 0  # Force stale

        cleanup2 = await sync.cleanup_stale(user_ids=["user_1"])
        check("Cleanup with stale data: cleaned >= 1",
              cleanup2["cleaned"] >= 1)

        # Restore original max_age
        DEFAULT_MAX_AGE["gmail"] = original_max_age

        # ----- Cleanup test files -----
        try:
            if os.path.exists(TEST_DB):
                os.remove(TEST_DB)
        except PermissionError:
            print("  [INFO] Could not delete test DB (locked, OK on Windows)")
        try:
            if os.path.exists(TEST_CHROMA):
                shutil.rmtree(TEST_CHROMA)
        except PermissionError:
            print("  [INFO] Could not delete test ChromaDB (locked, OK on Windows)")

        print()
        print("=" * 60)
        print(f"[RESULTS] {passed} passed, {failed} failed "
              f"out of {passed + failed} tests")

    asyncio.run(run_tests())

"""
=============================================================================
DATA FETCH SERVICE — Proactive Data Sync from Composio APIs
=============================================================================

WHY THIS EXISTS:
    Without this, a new user's first query is SLOW — it has to fetch data
    from Composio API on demand (1-3 seconds per tool). This service
    proactively syncs data so queries hit local cache instantly.

TWO MODES:
    1. INITIAL FETCH (on OAuth connect)
       User connects Gmail → paginated fetch (10/page, up to 100) → store locally
       User connects Slack → fetch recent messages → store locally

    2. PERIODIC SYNC (background, every 60s)
       For each connected user+tool: check for new data → UPSERT into DB
       Deduplication is automatic (SQLite UPSERT on external_id)

HOW IT CONNECTS:
    ┌───────────────────────────────────────────────────────────────┐
    │  User completes OAuth (frontend)                              │
    │      ↓                                                        │
    │  POST /api/sync/on-connected                                  │
    │      ↓                                                        │
    │  DataFetchService.initial_fetch(user_id, tool)                │
    │      ↓                                                        │
    │  GmailToolManager.execute("GMAIL_FETCH_EMAILS", ...)         │
    │      ↓                                                        │
    │  LocalToolManager.cache_results(user_id, tool, items)        │
    │      ↓                                                        │
    │  SQLite + ChromaDB now have data → queries hit cache          │
    └───────────────────────────────────────────────────────────────┘

    ┌───────────────────────────────────────────────────────────────┐
    │  Background loop (every 60s)                                  │
    │      ↓                                                        │
    │  Query connections table → get all connected users             │
    │      ↓                                                        │
    │  For each: check_new_data(user_id, tool)                      │
    │      ↓                                                        │
    │  Fetch recent items → UPSERT (dedup) → only new items stored  │
    └───────────────────────────────────────────────────────────────┘

USAGE:
    from services.data_fetch_service import get_data_fetch_service

    fetch = get_data_fetch_service()
    await fetch.initial_fetch("user_1", "gmail")       # After OAuth
    await fetch.start_periodic_sync(interval_seconds=60) # On startup
    await fetch.stop_periodic_sync()                     # On shutdown

=============================================================================
"""

import os
import sys
import logging
import asyncio

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from database import get_database
from tools.local_tools import get_local_tools
from tools.gmail_tools import GmailToolManager
from tools.slack_tools import SlackToolManager

logger = logging.getLogger(__name__)


class DataFetchService:
    """
    Proactive data sync: fetches from Composio APIs and stores locally.

    Two entry points:
        initial_fetch()  — called once after OAuth, fetches bulk data
        check_new_data() — called every 60s, fetches recent items (UPSERT dedup)
    """

    def __init__(self):
        self.local_tools = get_local_tools()
        self.db = get_database()
        self._sync_task = None
        self._running = False

    # =========================================================================
    # INITIAL FETCH — Called once after OAuth completes
    # =========================================================================

    async def initial_fetch(self, user_id, tool_name):
        """
        Fetch recent data for a newly connected tool.

        Gmail: paginated fetch — 10 emails per page, up to 100 total.
               Caches each batch immediately so partial data is saved even
               if a later page fails. Stops when no more pages or 100 reached.
        Slack: recent 20 messages via search

        Args:
            user_id   (str): The user who just connected
            tool_name (str): "gmail" or "slack"

        Returns:
            dict: {tool, count, status, error?}
        """
        logger.info(f"[DataFetch] Initial fetch: {user_id}/{tool_name}")

        try:
            if tool_name == "gmail":
                count = await self._fetch_gmail_paginated(
                    user_id, query="", page_size=10, max_total=100,
                )
                return {"tool": tool_name, "count": count, "status": "synced"}

            elif tool_name == "slack":
                items = await self._fetch_slack(user_id, limit=20)
                data_type = "message"
            else:
                return {
                    "tool": tool_name,
                    "count": 0,
                    "status": "unsupported",
                    "error": f"Tool '{tool_name}' not supported for sync",
                }

            if not items:
                logger.info(f"[DataFetch] No items returned for {user_id}/{tool_name}")
                return {"tool": tool_name, "count": 0, "status": "empty"}

            items = self._ensure_id_field(items, tool_name)

            result = await self.local_tools.cache_results(
                user_id=user_id,
                tool=tool_name,
                data_type=data_type,
                items=items,
            )

            count = result.get("sqlite_count", len(items))
            logger.info(f"[DataFetch] Initial fetch done: {user_id}/{tool_name} "
                        f"→ {count} items cached")

            return {"tool": tool_name, "count": count, "status": "synced"}

        except Exception as e:
            logger.error(f"[DataFetch] Initial fetch failed: {user_id}/{tool_name}: {e}")
            return {
                "tool": tool_name,
                "count": 0,
                "status": "error",
                "error": str(e),
            }

    # =========================================================================
    # CHECK NEW DATA — Called every 60s by background loop
    # =========================================================================

    async def check_new_data(self, user_id, tool_name):
        """
        Fetch recent items and store. UPSERT handles dedup automatically.

        Gmail: fetches with 'newer_than:1d' — only recent emails
        Slack: fetches recent messages

        Args:
            user_id   (str): User to sync
            tool_name (str): "gmail" or "slack"

        Returns:
            dict: {tool, new_count, status}
        """
        try:
            if tool_name == "gmail":
                items = await self._fetch_gmail(
                    user_id, query="newer_than:1d", max_results=20
                )
                data_type = "email"
            elif tool_name == "slack":
                items = await self._fetch_slack(user_id, limit=20)
                data_type = "message"
            else:
                return {"tool": tool_name, "new_count": 0, "status": "unsupported"}

            if not items:
                return {"tool": tool_name, "new_count": 0, "status": "no_new"}

            items = self._ensure_id_field(items, tool_name)

            # UPSERT — duplicates are skipped, only new items are added
            result = await self.local_tools.cache_results(
                user_id=user_id,
                tool=tool_name,
                data_type=data_type,
                items=items,
            )

            count = result.get("sqlite_count", 0)
            logger.info(f"[DataFetch] Periodic sync: {user_id}/{tool_name} "
                        f"→ {count} items (UPSERT)")

            return {"tool": tool_name, "new_count": count, "status": "synced"}

        except Exception as e:
            logger.error(f"[DataFetch] Periodic sync failed: "
                         f"{user_id}/{tool_name}: {e}")
            return {"tool": tool_name, "new_count": 0, "status": "error"}

    # =========================================================================
    # TOOL-SPECIFIC FETCHERS
    # =========================================================================

    async def _fetch_gmail(self, user_id, query="", max_results=10):
        """
        Fetch a single page of emails from Gmail via Composio.

        Composio SDK is synchronous, so we run it in a thread
        to avoid blocking the async event loop.

        Args:
            user_id     (str): User's entity_id for Composio
            query       (str): Gmail search query (e.g. "newer_than:1d")
            max_results (int): Max emails to fetch per page

        Returns:
            list[dict]: Normalized email dicts, or empty list on failure
        """
        try:
            tools = GmailToolManager(user_id=user_id)
            raw = await asyncio.to_thread(
                tools.execute,
                "GMAIL_FETCH_EMAILS",
                {"query": query, "max_results": max_results},
            )
            return self._normalize_response(raw)
        except Exception as e:
            logger.error(f"[DataFetch] Gmail fetch failed for {user_id}: {e}")
            return []

    async def _fetch_gmail_paginated(self, user_id, query="", page_size=10,
                                     max_total=100):
        """
        Fetch emails in pages (10 at a time) until max_total or no more pages.

        Each batch is cached immediately to SQLite + ChromaDB, so even if
        a later page fails (network error, quota), earlier pages are saved.

        If the user has fewer than max_total emails, it stops automatically
        when Gmail returns no nextPageToken.

        Args:
            user_id   (str): User's entity_id for Composio
            query     (str): Gmail search query
            page_size (int): Emails per page (default: 10)
            max_total (int): Stop after this many total emails (default: 100)

        Returns:
            int: Total number of emails cached across all pages
        """
        tools = GmailToolManager(user_id=user_id)
        total_cached = 0
        page_token = None
        page_num = 0

        while total_cached < max_total:
            page_num += 1
            batch_size = min(page_size, max_total - total_cached)

            # Build request params
            params = {"query": query, "max_results": batch_size}
            if page_token:
                params["page_token"] = page_token

            logger.info(f"[DataFetch] Gmail page {page_num}: fetching "
                        f"{batch_size} emails for {user_id} "
                        f"(total so far: {total_cached})")

            try:
                raw = await asyncio.to_thread(
                    tools.execute, "GMAIL_FETCH_EMAILS", params,
                )
            except Exception as e:
                logger.error(f"[DataFetch] Gmail page {page_num} failed: {e}")
                break  # Stop pagination, keep what we have

            # Extract nextPageToken BEFORE normalizing (normalize strips it)
            page_token = self._extract_page_token(raw)

            # Normalize into list of dicts
            items = self._normalize_response(raw)

            if not items:
                logger.info(f"[DataFetch] Gmail page {page_num}: empty, stopping")
                break

            # Ensure IDs + cache immediately
            items = self._ensure_id_field(items, "gmail")
            result = await self.local_tools.cache_results(
                user_id=user_id,
                tool="gmail",
                data_type="email",
                items=items,
            )

            batch_count = result.get("sqlite_count", len(items))
            total_cached += batch_count
            logger.info(f"[DataFetch] Gmail page {page_num}: "
                        f"cached {batch_count} emails "
                        f"(total: {total_cached})")

            # No more pages — user has fewer emails than max_total
            if not page_token:
                logger.info(f"[DataFetch] Gmail: no more pages, stopping "
                            f"at {total_cached} emails")
                break

        logger.info(f"[DataFetch] Gmail paginated fetch done: "
                     f"{user_id} → {total_cached} emails in {page_num} pages")
        return total_cached

    def _extract_page_token(self, raw_response):
        """
        Extract nextPageToken from raw Composio response before normalizing.

        Composio returns ToolExecuteResponse with .data dict that may
        contain nextPageToken from Gmail API.

        Args:
            raw_response: Raw Composio ToolExecuteResponse

        Returns:
            str or None: The next page token, or None if no more pages
        """
        # Try ToolExecuteResponse.data dict
        if hasattr(raw_response, "data") and isinstance(raw_response.data, dict):
            return (raw_response.data.get("nextPageToken")
                    or raw_response.data.get("next_page_token"))

        # Try response_data
        if hasattr(raw_response, "response_data"):
            rd = raw_response.response_data
            if isinstance(rd, dict):
                return (rd.get("nextPageToken")
                        or rd.get("next_page_token"))

        # Try plain dict
        if isinstance(raw_response, dict):
            return (raw_response.get("nextPageToken")
                    or raw_response.get("next_page_token"))

        return None

    async def _fetch_slack(self, user_id, limit=50):
        """
        Fetch messages from Slack via Composio.

        Uses SLACK_SEARCH_MESSAGES with a broad query to get recent messages.

        Args:
            user_id (str): User's entity_id for Composio
            limit   (int): Max messages to fetch

        Returns:
            list[dict]: Normalized message dicts, or empty list on failure
        """
        try:
            tools = SlackToolManager(user_id=user_id)
            raw = await asyncio.to_thread(
                tools.execute,
                "SLACK_SEARCH_MESSAGES",
                {"query": "in:*", "count": limit},
            )
            return self._normalize_response(raw)
        except Exception as e:
            logger.error(f"[DataFetch] Slack fetch failed for {user_id}: {e}")
            return []

    # =========================================================================
    # NORMALIZE — Convert Composio API responses to list of dicts
    # =========================================================================

    def _normalize_response(self, raw_response):
        """
        Normalize raw Composio API response into a list of dicts.

        Composio tools.execute() can return:
            - A list of dicts (direct data)
            - A dict with 'data' key containing list
            - A dict with 'results' or 'messages' key containing list
            - An object with .data attribute (ExecuteToolResponse)
            - A string (error or single value)

        Same pattern as BaseAgent._normalize_api_results().

        Args:
            raw_response: Whatever Composio returned

        Returns:
            list[dict]: Normalized items, or empty list
        """
        if raw_response is None:
            return []

        # Handle Composio ExecuteToolResponse objects
        if hasattr(raw_response, "data"):
            raw_response = raw_response.data
        if hasattr(raw_response, "response_data"):
            raw_response = raw_response.response_data

        if isinstance(raw_response, list):
            return raw_response

        if isinstance(raw_response, dict):
            # Check common wrapper keys
            for key in ("data", "results", "messages", "items", "emails"):
                if key in raw_response:
                    val = raw_response[key]
                    if isinstance(val, list):
                        return val
                    if isinstance(val, dict):
                        return [val]

            # If dict has typical data fields, treat it as a single item
            if any(k in raw_response for k in
                   ("id", "subject", "text", "messageId", "ts")):
                return [raw_response]

            return [raw_response]

        if isinstance(raw_response, str):
            return [{"content": raw_response}]

        return []

    def _ensure_id_field(self, items, tool_name):
        """
        Ensure each item has an 'id' field for SQLite UPSERT dedup.

        Different APIs use different ID fields:
            Gmail:   'messageId' or 'id'
            Slack:   'ts' (timestamp) or 'id'
            Outlook: 'id'

        Args:
            items     (list): List of item dicts
            tool_name (str):  Tool name for ID field mapping

        Returns:
            list[dict]: Items with 'id' field guaranteed
        """
        # Map of tool → possible ID field names (in priority order)
        id_fields = {
            "gmail": ["id", "messageId", "message_id", "threadId"],
            "slack": ["id", "ts", "client_msg_id", "iid"],
            "outlook": ["id", "messageId", "message_id"],
        }

        candidates = id_fields.get(tool_name, ["id"])

        for item in items:
            if not isinstance(item, dict):
                continue
            if "id" in item and item["id"]:
                continue  # Already has 'id'

            # Try each candidate field
            for field in candidates:
                if field in item and item[field]:
                    item["id"] = str(item[field])
                    break
            else:
                # No ID found — generate one from content hash
                import hashlib
                content_str = str(sorted(item.items()))
                item["id"] = hashlib.md5(
                    content_str.encode()
                ).hexdigest()[:16]

        return items

    # =========================================================================
    # BACKGROUND PERIODIC SYNC
    # =========================================================================

    async def start_periodic_sync(self, interval_seconds=60):
        """
        Start a background asyncio task that syncs data every N seconds.

        For each connected user+tool (from connections table),
        calls check_new_data() to fetch and store recent items.

        Args:
            interval_seconds (int): Seconds between sync cycles (default: 60)
        """
        if self._running:
            logger.warning("[DataFetch] Periodic sync already running")
            return

        self._running = True
        self._sync_task = asyncio.create_task(
            self._sync_loop(interval_seconds)
        )
        logger.info(f"[DataFetch] Periodic sync started "
                    f"(every {interval_seconds}s)")

    async def stop_periodic_sync(self):
        """Stop the background sync loop."""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
            logger.info("[DataFetch] Periodic sync stopped")

    async def _sync_loop(self, interval_seconds):
        """
        The background sync loop.

        Every cycle:
            1. Query connections table for all connected users
            2. For each user+tool: check_new_data()
            3. Log summary
        """
        while self._running:
            await asyncio.sleep(interval_seconds)
            try:
                await self._run_sync_cycle()
            except Exception as e:
                logger.error(f"[DataFetch] Sync cycle error: {e}")

    async def _run_sync_cycle(self):
        """
        Run one sync cycle: fetch new data for all connected users.

        Reads the connections table to find who is connected to what.
        """
        try:
            # Get all unique users who have connections
            rows = await self.db.execute_sql(
                "SELECT DISTINCT user_id, tool FROM connections "
                "WHERE status = 'connected'"
            )
        except Exception as e:
            logger.error(f"[DataFetch] Could not query connections: {e}")
            return

        if not rows:
            return  # No connected users

        synced = 0
        for row in rows:
            user_id = row["user_id"]
            tool = row["tool"]
            try:
                result = await self.check_new_data(user_id, tool)
                if result["status"] == "synced":
                    synced += 1
            except Exception as e:
                logger.error(f"[DataFetch] Sync failed for "
                             f"{user_id}/{tool}: {e}")

        if synced > 0:
            logger.info(f"[DataFetch] Sync cycle done: "
                        f"{synced}/{len(rows)} tools synced")


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_fetch_instance = None


def get_data_fetch_service():
    """
    Get the singleton DataFetchService instance.

    Usage:
        from services.data_fetch_service import get_data_fetch_service
        fetch = get_data_fetch_service()
        await fetch.initial_fetch("user_1", "gmail")
    """
    global _fetch_instance
    if _fetch_instance is None:
        _fetch_instance = DataFetchService()
    return _fetch_instance

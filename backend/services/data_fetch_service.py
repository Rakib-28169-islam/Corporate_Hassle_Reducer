"""DataFetchService — proactive data sync from Composio APIs."""

import logging
import asyncio

from database import get_database
from tools.local_tools import get_local_tools
from tools.gmail_tools import GmailToolManager
from tools.slack_tools import SlackToolManager

logger = logging.getLogger(__name__)


class DataFetchService:
    """Proactive data sync: fetches from Composio APIs and stores locally."""

    def __init__(self):
        self.local_tools = get_local_tools()
        self.db = get_database()
        self._sync_task = None
        self._running = False

    async def initial_fetch(self, user_id, tool_name):
        """Fetch recent data for a newly connected tool.

        Skips full fetch if data already exists and is fresh (< 30 min old).
        For returning connections, does a light sync instead of full paginated fetch.
        """
        logger.info(f"[DataFetch] Initial fetch: {user_id}/{tool_name}")

        # Check if we already have cached data for this user+tool
        existing_count = await self.local_tools.db.get_memory_count(user_id, tool=tool_name)
        if existing_count > 0:
            is_stale = await self.local_tools.is_stale(user_id, tool_name, max_age_minutes=30)
            if not is_stale:
                logger.info(f"[DataFetch] Skipping initial fetch: {user_id}/{tool_name} "
                            f"already has {existing_count} items (fresh)")
                return {"tool": tool_name, "count": existing_count,
                        "status": "already_synced"}

            # Data exists but is stale — do a light sync instead of full fetch
            logger.info(f"[DataFetch] Existing data stale, doing light sync: "
                        f"{user_id}/{tool_name} ({existing_count} items)")
            result = await self.check_new_data(user_id, tool_name)
            total = existing_count + result.get("new_count", 0)
            return {"tool": tool_name, "count": total, "status": "synced"}

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
                    "tool": tool_name, "count": 0, "status": "unsupported",
                    "error": f"Tool '{tool_name}' not supported for sync",
                }

            if not items:
                logger.info(f"[DataFetch] No items returned for {user_id}/{tool_name}")
                return {"tool": tool_name, "count": 0, "status": "empty"}

            items = self._ensure_id_field(items, tool_name)
            result = await self.local_tools.cache_results(
                user_id=user_id, tool=tool_name, data_type=data_type, items=items,
            )

            count = result.get("sqlite_count", len(items))
            logger.info(f"[DataFetch] Initial fetch done: {user_id}/{tool_name} -> {count} items")
            return {"tool": tool_name, "count": count, "status": "synced"}

        except Exception as e:
            logger.error(f"[DataFetch] Initial fetch failed: {user_id}/{tool_name}: {e}")
            return {"tool": tool_name, "count": 0, "status": "error", "error": str(e)}

    async def check_new_data(self, user_id, tool_name):
        """Fetch recent items and UPSERT. Called every 60s by background loop."""
        try:
            if tool_name == "gmail":
                items = await self._fetch_gmail(user_id, query="newer_than:1d", max_results=20)
                data_type = "email"
            elif tool_name == "slack":
                items = await self._fetch_slack(user_id, limit=20)
                data_type = "message"
            else:
                return {"tool": tool_name, "new_count": 0, "status": "unsupported"}

            if not items:
                return {"tool": tool_name, "new_count": 0, "status": "no_new"}

            items = self._ensure_id_field(items, tool_name)
            result = await self.local_tools.cache_results(
                user_id=user_id, tool=tool_name, data_type=data_type, items=items,
            )

            count = result.get("sqlite_count", 0)
            logger.info(f"[DataFetch] Periodic sync: {user_id}/{tool_name} -> {count} items")
            return {"tool": tool_name, "new_count": count, "status": "synced"}

        except Exception as e:
            logger.error(f"[DataFetch] Periodic sync failed: {user_id}/{tool_name}: {e}")
            return {"tool": tool_name, "new_count": 0, "status": "error"}

    # --- Tool-specific fetchers ---

    async def _fetch_gmail(self, user_id, query="", max_results=10):
        """Fetch a single page of emails from Gmail via Composio."""
        try:
            tools = GmailToolManager(user_id=user_id)
            raw = await asyncio.to_thread(
                tools.execute, "GMAIL_FETCH_EMAILS",
                {"query": query, "max_results": max_results},
            )
            items = self._normalize_response(raw)
            return self.normalize_tool_fields(items, "gmail")
        except Exception as e:
            logger.error(f"[DataFetch] Gmail fetch failed for {user_id}: {e}")
            return []

    async def _fetch_gmail_paginated(self, user_id, query="", page_size=10, max_total=100):
        """Fetch emails in pages (10 at a time), caching each batch immediately."""
        tools = GmailToolManager(user_id=user_id)
        total_cached = 0
        page_token = None
        page_num = 0

        while total_cached < max_total:
            page_num += 1
            batch_size = min(page_size, max_total - total_cached)

            batch_count, page_token = await self._fetch_and_cache_page(
                tools, user_id, query, batch_size, page_token, page_num,
            )

            if batch_count == 0:
                break

            total_cached += batch_count

            if not page_token:
                logger.info(f"[DataFetch] Gmail: no more pages, stopping at {total_cached}")
                break

        logger.info(f"[DataFetch] Gmail paginated fetch done: "
                    f"{user_id} -> {total_cached} emails in {page_num} pages")
        return total_cached

    async def _fetch_and_cache_page(self, tools, user_id, query, batch_size, page_token, page_num):
        """Fetch one page of Gmail emails and cache them. Returns (count, next_token)."""
        params = {"query": query, "max_results": batch_size}
        if page_token:
            params["page_token"] = page_token

        logger.info(f"[DataFetch] Gmail page {page_num}: fetching {batch_size} emails for {user_id}")

        try:
            raw = await asyncio.to_thread(tools.execute, "GMAIL_FETCH_EMAILS", params)
        except Exception as e:
            logger.error(f"[DataFetch] Gmail page {page_num} failed: {e}")
            return 0, None

        next_token = self._extract_page_token(raw)
        items = self._normalize_response(raw)
        items = self.normalize_tool_fields(items, "gmail")

        if not items:
            logger.info(f"[DataFetch] Gmail page {page_num}: empty, stopping")
            return 0, None

        items = self._ensure_id_field(items, "gmail")
        result = await self.local_tools.cache_results(
            user_id=user_id, tool="gmail", data_type="email", items=items,
        )

        batch_count = result.get("sqlite_count", len(items))
        logger.info(f"[DataFetch] Gmail page {page_num}: cached {batch_count} emails")
        return batch_count, next_token

    def _extract_page_token(self, raw_response):
        """Extract nextPageToken from raw Composio response."""
        if hasattr(raw_response, "data") and isinstance(raw_response.data, dict):
            return (raw_response.data.get("nextPageToken")
                    or raw_response.data.get("next_page_token"))

        if hasattr(raw_response, "response_data"):
            rd = raw_response.response_data
            if isinstance(rd, dict):
                return rd.get("nextPageToken") or rd.get("next_page_token")

        if isinstance(raw_response, dict):
            return raw_response.get("nextPageToken") or raw_response.get("next_page_token")

        return None

    async def _fetch_slack(self, user_id, limit=50):
        """Fetch messages from Slack via Composio."""
        try:
            tools = SlackToolManager(user_id=user_id)
            raw = await asyncio.to_thread(
                tools.execute, "SLACK_SEARCH_MESSAGES",
                {"query": "in:*", "count": limit},
            )
            return self._normalize_response(raw)
        except Exception as e:
            logger.error(f"[DataFetch] Slack fetch failed for {user_id}: {e}")
            return []

    # --- Response normalization ---

    def _normalize_response(self, raw_response):
        """Normalize Composio API response into a list of dicts."""
        if raw_response is None:
            return []

        if hasattr(raw_response, "data"):
            raw_response = raw_response.data
        if hasattr(raw_response, "response_data"):
            raw_response = raw_response.response_data

        if isinstance(raw_response, list):
            return raw_response

        if isinstance(raw_response, dict):
            for key in ("data", "results", "messages", "items", "emails"):
                if key in raw_response:
                    val = raw_response[key]
                    if isinstance(val, list):
                        return val
                    if isinstance(val, dict):
                        return [val]

            if any(k in raw_response for k in ("id", "subject", "text", "messageId", "ts")):
                return [raw_response]
            return [raw_response]

        if isinstance(raw_response, str):
            return [{"content": raw_response}]

        return []

    # --- Field normalization (Composio → canonical names) ---

    # Composio field name → our canonical field name
    GMAIL_FIELD_MAP = {
        "sender": "from",
        "messageText": "body",
        "messageTimestamp": "date",
    }
    # Fields to drop (too large or redundant)
    GMAIL_DROP_FIELDS = {"payload", "preview"}

    def normalize_tool_fields(self, items, tool_name):
        """Normalize Composio-specific field names to our canonical schema.

        Gmail: sender→from, messageText→body, messageTimestamp→date,
               labelIds→unread (bool), attachmentList→has_attachment (bool).
        """
        if tool_name == "gmail":
            return [self._normalize_gmail(item) for item in items
                    if isinstance(item, dict)]
        return items

    def _normalize_gmail(self, item):
        """Normalize a single Gmail item to canonical fields."""
        normalized = {}
        for key, value in item.items():
            if key in self.GMAIL_DROP_FIELDS:
                continue
            canonical = self.GMAIL_FIELD_MAP.get(key, key)
            normalized[canonical] = value

        # Derive boolean fields from Gmail-specific structures
        label_ids = item.get("labelIds", [])
        if isinstance(label_ids, list):
            normalized["unread"] = "UNREAD" in label_ids
            normalized["labels"] = label_ids

        attachment_list = item.get("attachmentList", [])
        if isinstance(attachment_list, list):
            normalized["has_attachment"] = len(attachment_list) > 0

        return normalized

    def _ensure_id_field(self, items, tool_name):
        """Ensure each item has an 'id' field for SQLite UPSERT dedup."""
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
                continue

            for field in candidates:
                if field in item and item[field]:
                    item["id"] = str(item[field])
                    break
            else:
                import hashlib
                content_str = str(sorted(item.items()))
                item["id"] = hashlib.md5(content_str.encode()).hexdigest()[:16]

        return items

    # --- Background periodic sync ---

    async def start_periodic_sync(self, interval_seconds=60):
        """Start background sync task that checks for new data every N seconds."""
        if self._running:
            logger.warning("[DataFetch] Periodic sync already running")
            return

        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop(interval_seconds))
        logger.info(f"[DataFetch] Periodic sync started (every {interval_seconds}s)")

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
        """Background loop: sync all connected users every cycle."""
        while self._running:
            await asyncio.sleep(interval_seconds)
            try:
                await self._run_sync_cycle()
            except Exception as e:
                logger.error(f"[DataFetch] Sync cycle error: {e}")

    async def _run_sync_cycle(self):
        """Run one sync cycle for all connected users."""
        try:
            rows = await self.db.execute_sql(
                "SELECT DISTINCT user_id, tool FROM connections "
                "WHERE status = 'connected'"
            )
        except Exception as e:
            logger.error(f"[DataFetch] Could not query connections: {e}")
            return

        if not rows:
            return

        synced = 0
        for row in rows:
            try:
                result = await self.check_new_data(row["user_id"], row["tool"])
                if result["status"] == "synced":
                    synced += 1
            except Exception as e:
                logger.error(f"[DataFetch] Sync failed for {row['user_id']}/{row['tool']}: {e}")

        if synced > 0:
            logger.info(f"[DataFetch] Sync cycle done: {synced}/{len(rows)} tools synced")


_fetch_instance = None


def get_data_fetch_service():
    """Get the singleton DataFetchService instance."""
    global _fetch_instance
    if _fetch_instance is None:
        _fetch_instance = DataFetchService()
    return _fetch_instance

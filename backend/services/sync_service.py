"""SyncService — cache invalidation & staleness cleanup for local data stores."""

import logging
import asyncio

from tools.local_tools import get_local_tools

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE = {
    "gmail": 30,
    "slack": 15,
    "outlook": 30,
    "calendar": 60,
}

CLEANUP_INTERVAL_MINUTES = 30


class SyncService:
    """Reactive + periodic cache invalidation via LocalToolManager."""

    def __init__(self):
        self.local_tools = get_local_tools()
        self._cleanup_task = None
        self._running = False
        logger.info("SyncService initialized")

    async def on_action_complete(self, user_id, tool, action_type=None):
        """Invalidate cache for user+tool after a mutation (send/delete/move)."""
        logger.info(f"Reactive invalidation: {user_id}/{tool} "
                     f"(action: {action_type or 'unknown'})")

        result = await self.local_tools.invalidate(user_id, tool)

        return {
            "invalidated": result["sqlite_ok"] and result["chromadb_ok"],
            "tool": tool,
            "action": action_type or "unknown",
            "errors": result.get("errors", []),
        }

    async def force_refresh(self, user_id, tool=None):
        """Force-clear cached data for a user, optionally scoped to one tool."""
        logger.info(f"Force refresh: {user_id}/{tool or 'ALL'}")

        result = await self.local_tools.invalidate(user_id, tool)

        return {
            "refreshed": result["sqlite_ok"] and result["chromadb_ok"],
            "tool": tool or "ALL",
            "errors": result.get("errors", []),
        }

    async def check_staleness(self, user_id, tool):
        """Check if a user+tool cache exceeds its max age threshold."""
        max_age = DEFAULT_MAX_AGE.get(tool, 30)
        is_stale = await self.local_tools.is_stale(user_id, tool, max_age)

        return {
            "stale": is_stale,
            "tool": tool,
            "max_age_min": max_age,
        }

    async def cleanup_stale(self, user_ids=None):
        """Scan all tracked users and invalidate caches that exceed max age."""
        if not user_ids:
            user_ids = await self._get_tracked_users()
            if not user_ids:
                return {"checked": 0, "cleaned": 0, "details": []}

        checked = 0
        cleaned = 0
        details = []

        for uid in user_ids:
            for tool in DEFAULT_MAX_AGE:
                checked += 1
                detail = await self._check_and_clean(uid, tool)
                if detail.get("cleaned"):
                    cleaned += 1
                details.append(detail)

        logger.info(f"Staleness cleanup: checked {checked}, cleaned {cleaned}")
        return {"checked": checked, "cleaned": cleaned, "details": details}

    async def _get_tracked_users(self):
        """Get list of user IDs that have cached data."""
        try:
            rows = await self.local_tools.db.execute_sql(
                "SELECT DISTINCT user_id FROM memory"
            )
            return [r["user_id"] for r in rows] if rows else []
        except Exception as e:
            logger.error(f"Could not get user list for cleanup: {e}")
            return []

    async def _check_and_clean(self, uid, tool):
        """Check staleness for one user+tool and clean if needed."""
        max_age = DEFAULT_MAX_AGE.get(tool, 30)
        is_stale = await self.local_tools.is_stale(uid, tool, max_age)

        detail = {"user": uid, "tool": tool, "stale": is_stale}

        if is_stale:
            count = await self.local_tools.get_local_count(uid, tool)
            if count["has_data"]:
                await self.local_tools.invalidate(uid, tool)
                detail["cleaned"] = True
            else:
                detail["cleaned"] = False

        return detail

    async def start_background_cleanup(self, interval_minutes=None):
        """Start background asyncio loop that periodically cleans stale caches."""
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
        """Stop the background cleanup loop and cancel its task."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Background cleanup stopped")


_sync_instance = None


def get_sync_service():
    """Get the singleton SyncService instance."""
    global _sync_instance
    if _sync_instance is None:
        _sync_instance = SyncService()
    return _sync_instance

"""Tests extracted from sync_service.py — run with: python test/test_sync_inline.py"""

import os
import sys
import shutil
import asyncio

sys.stdout.reconfigure(encoding='utf-8')

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from database.sqlite_manager import DatabaseManager
from database.vector_store import VectorStore
from tools.local_tools import LocalToolManager
from services.sync_service import SyncService, DEFAULT_MAX_AGE

TEST_DB = os.path.join(_backend_dir, "test", "test_sync_inline.db")
TEST_CHROMA = os.path.join(_backend_dir, "test", "test_sync_inline_chroma")


async def run_tests():
    print("Testing SyncService...")
    print("=" * 60)

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


if __name__ == "__main__":
    asyncio.run(run_tests())

"""
=============================================================================
PHASE 6 — Data Fetch Service Tests
=============================================================================

Tests the DataFetchService without hitting real Composio APIs.
Mocks tool managers, verifies normalization, dedup, and sync loop.

Run: python test/test_data_sync.py
=============================================================================
"""

import sys
import os
import asyncio
import shutil

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_test_dir = os.path.dirname(os.path.abspath(__file__))
TEST_DB = os.path.join(_test_dir, "test_data_sync.db")
TEST_CHROMA = os.path.join(_test_dir, "test_data_sync_chroma")


async def run_tests():
    print("Phase 6: DataFetchService Tests")
    print("=" * 60)

    from database.sqlite_manager import DatabaseManager
    from database.vector_store import VectorStore
    from tools.local_tools import LocalToolManager
    from services.data_fetch_service import DataFetchService

    # Clean previous
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    try:
        if os.path.exists(TEST_CHROMA):
            shutil.rmtree(TEST_CHROMA)
    except PermissionError:
        pass

    # Setup test infra
    db = DatabaseManager(db_path=TEST_DB)
    await db.init_db()
    vs = VectorStore(persist_dir=TEST_CHROMA)

    lt = LocalToolManager()
    lt.db = db
    lt.vs = vs

    # Create service with test dependencies
    service = DataFetchService()
    service.local_tools = lt
    service.db = db

    passed = 0
    failed = 0

    def check(name, condition):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  [OK] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}")

    # =================================================================
    # TEST 1: _normalize_response — various formats
    # =================================================================
    print("\n--- 1. _normalize_response ---")

    # List input → passthrough
    data1 = service._normalize_response([{"id": "1", "subject": "Hi"}])
    check("List input → list output",
          len(data1) == 1 and data1[0]["id"] == "1")

    # Dict with 'data' key → extract list
    data2 = service._normalize_response(
        {"data": [{"id": "2", "subject": "Hello"}]}
    )
    check("Dict with 'data' key → extracted list",
          len(data2) == 1 and data2[0]["id"] == "2")

    # Dict with 'messages' key → extract list
    data3 = service._normalize_response(
        {"messages": [{"ts": "123", "text": "hey"}]}
    )
    check("Dict with 'messages' key → extracted list",
          len(data3) == 1 and data3[0]["ts"] == "123")

    # Dict with 'results' key → extract list
    data4 = service._normalize_response(
        {"results": [{"id": "3"}]}
    )
    check("Dict with 'results' key → extracted list",
          len(data4) == 1 and data4[0]["id"] == "3")

    # None → empty list
    data5 = service._normalize_response(None)
    check("None → empty list", data5 == [])

    # String → wrapped in list
    data6 = service._normalize_response("some error text")
    check("String → list with content key",
          len(data6) == 1 and data6[0]["content"] == "some error text")

    # Single dict with data fields → list of one item
    data7 = service._normalize_response(
        {"id": "4", "subject": "Single email"}
    )
    check("Single dict with 'id' → list of one item",
          len(data7) == 1 and data7[0]["subject"] == "Single email")

    # =================================================================
    # TEST 2: _ensure_id_field — maps tool-specific IDs
    # =================================================================
    print("\n--- 2. _ensure_id_field ---")

    # Gmail: messageId → id
    gmail_items = [{"messageId": "abc123", "subject": "Test"}]
    result = service._ensure_id_field(gmail_items, "gmail")
    check("Gmail: messageId mapped to id",
          result[0].get("id") == "abc123")

    # Gmail: already has id → unchanged
    gmail_items2 = [{"id": "existing", "subject": "Test"}]
    result2 = service._ensure_id_field(gmail_items2, "gmail")
    check("Gmail: existing id preserved",
          result2[0]["id"] == "existing")

    # Slack: ts → id
    slack_items = [{"ts": "1708123456.000100", "text": "Hello"}]
    result3 = service._ensure_id_field(slack_items, "slack")
    check("Slack: ts mapped to id",
          result3[0].get("id") == "1708123456.000100")

    # No ID field → generates hash
    no_id_items = [{"subject": "No ID here", "body": "Content"}]
    result4 = service._ensure_id_field(no_id_items, "gmail")
    check("No ID field → generates hash id",
          "id" in result4[0] and len(result4[0]["id"]) > 0)

    # =================================================================
    # TEST 3: initial_fetch stores data in DB (mocked paginated fetcher)
    # =================================================================
    print("\n--- 3. initial_fetch (mocked) ---")

    fake_emails = [
        {"id": "email_001", "subject": "Invoice", "from": "a@b.com",
         "body": "Amount: $100"},
        {"id": "email_002", "subject": "Meeting", "from": "c@d.com",
         "body": "Tomorrow at 3pm"},
        {"id": "email_003", "subject": "Report", "from": "e@f.com",
         "body": "Q4 numbers ready"},
    ]

    # Mock _fetch_gmail_paginated — simulates caching 3 items and returning count
    original_fetch_paginated = service._fetch_gmail_paginated
    original_fetch_gmail = service._fetch_gmail

    async def mock_fetch_paginated(user_id, query="", page_size=10,
                                    max_total=100):
        # Simulate what paginated does: ensure IDs, cache, return count
        items = service._ensure_id_field(list(fake_emails), "gmail")
        result = await service.local_tools.cache_results(
            user_id=user_id, tool="gmail", data_type="email", items=items,
        )
        return result.get("sqlite_count", len(items))

    service._fetch_gmail_paginated = mock_fetch_paginated

    result = await service.initial_fetch("test_user", "gmail")
    check(f"initial_fetch status: {result['status']}",
          result["status"] == "synced")
    check(f"initial_fetch count: {result['count']} (expected 3)",
          result["count"] == 3)

    # Verify data is in SQLite
    count = await db.get_memory_count("test_user", tool="gmail")
    check(f"SQLite has {count} gmail items (expected 3)", count == 3)

    # Verify data is searchable
    search = await db.search_memory("test_user", tool="gmail", query="Invoice")
    check("Cached email searchable by keyword",
          len(search) >= 1)

    # Restore
    service._fetch_gmail_paginated = original_fetch_paginated

    # =================================================================
    # TEST 4: check_new_data deduplicates via UPSERT
    # =================================================================
    print("\n--- 4. check_new_data dedup ---")

    # Now add overlapping + new items
    overlapping_plus_new = [
        {"id": "email_001", "subject": "Invoice UPDATED", "from": "a@b.com",
         "body": "Updated amount: $200"},  # Existing → UPDATE
        {"id": "email_004", "subject": "New Email", "from": "g@h.com",
         "body": "Brand new"},  # New → INSERT
        {"id": "email_005", "subject": "Another New", "from": "i@j.com",
         "body": "Also new"},  # New → INSERT
    ]

    async def mock_fetch_gmail_new(user_id, query="", max_results=20):
        return overlapping_plus_new

    service._fetch_gmail = mock_fetch_gmail_new

    result2 = await service.check_new_data("test_user", "gmail")
    check(f"check_new_data status: {result2['status']}",
          result2["status"] == "synced")

    # Total should be 5 (3 original + 2 new), not 6 (dedup handled email_001)
    total = await db.get_memory_count("test_user", tool="gmail")
    check(f"After dedup: total={total} (expected 5)", total == 5)

    # Verify the updated content
    updated = await db.search_memory("test_user", tool="gmail", query="UPDATED")
    check("UPSERT updated existing email content",
          len(updated) >= 1)

    service._fetch_gmail = original_fetch_gmail

    # =================================================================
    # TEST 5: initial_fetch for Slack (mocked)
    # =================================================================
    print("\n--- 5. Slack initial_fetch ---")

    fake_slack = [
        {"id": "slack_001", "text": "Deploy ready", "channel": "#dev",
         "user": "john"},
        {"id": "slack_002", "text": "Bug found", "channel": "#bugs",
         "user": "sarah"},
    ]

    original_fetch_slack = service._fetch_slack

    async def mock_fetch_slack(user_id, limit=50):
        return fake_slack

    service._fetch_slack = mock_fetch_slack

    slack_result = await service.initial_fetch("test_user", "slack")
    check(f"Slack initial_fetch: {slack_result['count']} items (expected 2)",
          slack_result["count"] == 2 and slack_result["status"] == "synced")

    slack_count = await db.get_memory_count("test_user", tool="slack")
    check(f"Slack in SQLite: {slack_count} (expected 2)", slack_count == 2)

    service._fetch_slack = original_fetch_slack

    # =================================================================
    # TEST 6: Unsupported tool returns error
    # =================================================================
    print("\n--- 6. Error handling ---")

    unsupported = await service.initial_fetch("test_user", "twitter")
    check("Unsupported tool returns 'unsupported' status",
          unsupported["status"] == "unsupported")

    # =================================================================
    # TEST 7: Connection tracking + sync loop
    # =================================================================
    print("\n--- 7. Connection tracking ---")

    # Save a connection
    await db.save_connection(
        connection_id="conn_test_001",
        user_id="test_user",
        tool="gmail",
        composio_id="comp_test",
        status="connected",
    )

    # Verify connection saved
    connections = await db.get_connections("test_user")
    gmail_conn = [c for c in connections if c["tool"] == "gmail"]
    check("Connection saved to DB",
          len(gmail_conn) == 1 and gmail_conn[0]["status"] == "connected")

    # Run one sync cycle (with mock)
    async def mock_fetch_gmail_sync(user_id, query="", max_results=20):
        return [{"id": "email_006", "subject": "New from sync",
                 "from": "sync@test.com", "body": "Auto-synced"}]

    service._fetch_gmail = mock_fetch_gmail_sync

    await service._run_sync_cycle()

    # Verify new email was added
    total_after_sync = await db.get_memory_count("test_user", tool="gmail")
    check(f"After sync cycle: total={total_after_sync} (expected 6)",
          total_after_sync == 6)

    service._fetch_gmail = original_fetch_gmail

    # =================================================================
    # TEST 8: Start/stop periodic sync
    # =================================================================
    print("\n--- 8. Periodic sync lifecycle ---")

    await service.start_periodic_sync(interval_seconds=9999)
    check("Periodic sync started", service._running is True)
    check("Sync task exists", service._sync_task is not None)

    await service.stop_periodic_sync()
    check("Periodic sync stopped", service._running is False)
    check("Sync task cleared", service._sync_task is None)

    # =================================================================
    # TEST 9: User + connection created via on-connected flow
    # =================================================================
    print("\n--- 9. Full on-connected flow ---")

    await db.create_user("new_user", name="New User")
    user = await db.get_user("new_user")
    check("User created in DB", user is not None and user["name"] == "New User")

    await db.save_connection(
        connection_id="conn_new",
        user_id="new_user",
        tool="slack",
        status="connected",
    )
    new_conns = await db.get_connections("new_user")
    check("New user has slack connection",
          any(c["tool"] == "slack" for c in new_conns))

    # Cleanup
    try:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
    except PermissionError:
        pass
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

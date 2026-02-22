"""Inline smoke tests extracted from sqlite_manager.py."""

import sys
import os
import asyncio

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database.sqlite_manager import DatabaseManager


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
    os.remove("test_hassle_reducer.db")
    print("\n" + "=" * 50)
    print("[ALL TESTS PASSED]")


if __name__ == "__main__":
    asyncio.run(test())

"""Inline tests extracted from tools/local_tools.py."""

import asyncio
import sys
import os
import shutil

sys.stdout.reconfigure(encoding='utf-8')

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from tools.local_tools import LocalToolManager, SMART_THRESHOLD
from database.sqlite_manager import DatabaseManager
from database.vector_store import VectorStore

TEST_DB = os.path.join(_backend_dir, "test_local_tools.db")
TEST_CHROMA = os.path.join(_backend_dir, "test_local_tools_chroma")


async def run_tests():
    print("Testing LocalToolManager...")
    print("=" * 60)

    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    if os.path.exists(TEST_CHROMA):
        shutil.rmtree(TEST_CHROMA)

    db = DatabaseManager(db_path=TEST_DB)
    await db.init_db()
    vs = VectorStore(persist_dir=TEST_CHROMA)

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

    # Test 1: Empty search returns empty
    r = await lt.search_local("user_1", "gmail", "anything")
    check("Empty DB search returns empty",
          r["source"] == "empty" and len(r["results"]) == 0)

    # Test 2: Cache single item
    email1 = {
        "subject": "Team standup notes",
        "from": "john@company.com",
        "body": "Discussed deployment timeline and release date.",
    }
    cr = await lt.cache_single("user_1", "gmail", "email", email1, "msg_001")
    check("Cache single: SQLite OK", cr["sqlite_ok"] is True)
    check("Cache single: ChromaDB OK", cr["chromadb_ok"] is True)

    # Test 3: Cache batch items
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

    # Test 4: SQLite keyword search (threshold met)
    r = await lt.search_local("user_1", "gmail")
    check(f"Threshold MET: SQLite returned {r['sqlite_count']} (>= {SMART_THRESHOLD})",
          r["threshold_met"] is True and r["sqlite_count"] >= SMART_THRESHOLD)
    check("Source is 'sqlite' when threshold met", r["source"] == "sqlite")

    # Test 5: SQLite search with specific query (threshold NOT met)
    r = await lt.search_local("user_1", "gmail", "invoice")
    check(f"Threshold NOT met: SQLite returned {r['sqlite_count']} for 'invoice'",
          r["sqlite_count"] < SMART_THRESHOLD)

    # Test 6: Semantic search via ChromaDB
    r = await lt.search_local("user_1", "gmail", "deployment")
    check(f"Semantic fallback: found results for 'deployment' "
          f"(sqlite={r['sqlite_count']}, chromadb={r['chromadb_count']})",
          len(r["results"]) > 0)

    # Test 7: get_local_count
    counts = await lt.get_local_count("user_1", "gmail")
    check(f"Local count: SQLite={counts['sqlite_count']}, has_data={counts['has_data']}",
          counts["sqlite_count"] == 5 and counts["has_data"] is True)

    # Test 8: is_stale (data just cached = fresh)
    stale = await lt.is_stale("user_1", "gmail", max_age_minutes=30)
    check("Freshly cached data is NOT stale (max_age=30min)", stale is False)

    # Test 9: is_stale (very short max_age = stale)
    stale2 = await lt.is_stale("user_1", "gmail", max_age_minutes=0)
    check("max_age=0 min -> data IS stale", stale2 is True)

    # Test 10: is_stale (no data = stale)
    stale3 = await lt.is_stale("user_99", "gmail", max_age_minutes=30)
    check("No data for user -> IS stale", stale3 is True)

    # Test 11: Invalidate cache
    inv = await lt.invalidate("user_1", "gmail")
    check("Invalidate SQLite OK", inv["sqlite_ok"] is True)
    check("Invalidate ChromaDB OK", inv["chromadb_ok"] is True)

    r_after = await lt.search_local("user_1", "gmail", "anything")
    check("After invalidation: search returns empty",
          r_after["source"] == "empty" and len(r_after["results"]) == 0)

    count_after = await lt.get_local_count("user_1", "gmail")
    check("After invalidation: SQLite count = 0",
          count_after["sqlite_count"] == 0)

    # Cleanup
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


if __name__ == "__main__":
    asyncio.run(run_tests())

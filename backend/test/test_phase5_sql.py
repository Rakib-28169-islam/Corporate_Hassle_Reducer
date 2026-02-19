"""
=============================================================================
PHASE 5 TESTS — LLM SQL Generator for Smart Search
=============================================================================

Tests three areas:
  1. SQLReviewer safety (10 cases) — rule-based validation
  2. Integration with seeded data — LLM SQL precision vs LIKE
  3. Full pipeline — generate -> review -> execute -> fallback

Runs WITHOUT API keys for Group 1 (pure rule-based).
Groups 2 & 3 require GROQ_API_KEY for LLM SQL generation.

Run with:
    cd backend
    python test/test_phase5_sql.py
=============================================================================
"""

import sys
import os
import asyncio
import json
import shutil

# Setup path
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _backend_dir)
sys.stdout.reconfigure(encoding='utf-8')

passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


async def run_tests():
    global passed, failed

    print("Phase 5: LLM SQL Generator Tests")
    print("=" * 60)

    # =================================================================
    # TEST GROUP 1: SQLReviewer Safety (no API keys needed)
    # =================================================================
    print("\n--- 1. SQLReviewer Safety ---")

    from core.sql_generator import SQLReviewer

    reviewer = SQLReviewer()
    user_id = "test_user"

    # --- Should REJECT ---

    # 1. DROP TABLE
    r = reviewer.review("DROP TABLE memory", user_id)
    check("Rejects DROP TABLE", r["approved"] is False)

    # 2. DELETE statement
    r = reviewer.review("DELETE FROM memory WHERE user_id='test_user'", user_id)
    check("Rejects DELETE", r["approved"] is False)

    # 3. Wrong table (users instead of memory)
    r = reviewer.review(
        "SELECT * FROM users WHERE id='test_user' LIMIT 10", user_id
    )
    check("Rejects wrong table (users)", r["approved"] is False)

    # 4. No user_id filter
    r = reviewer.review(
        "SELECT * FROM memory LIMIT 10", user_id
    )
    check("Rejects missing user_id filter", r["approved"] is False)

    # 5. Semicolon (multi-statement injection)
    r = reviewer.review(
        "SELECT * FROM memory WHERE user_id='test_user'; DROP TABLE memory",
        user_id
    )
    check("Rejects semicolon injection", r["approved"] is False)

    # 6. System table (sqlite_master)
    r = reviewer.review(
        "SELECT * FROM sqlite_master WHERE user_id='test_user'", user_id
    )
    check("Rejects sqlite_master access", r["approved"] is False)

    # 7. LIMIT too high
    r = reviewer.review(
        "SELECT * FROM memory WHERE user_id = 'test_user' LIMIT 999",
        user_id
    )
    check("Rejects LIMIT > 100", r["approved"] is False)

    # 8. SQL comment
    r = reviewer.review(
        "-- comment\nSELECT * FROM memory WHERE user_id='test_user'",
        user_id
    )
    check("Rejects SQL comments (--)", r["approved"] is False)

    # --- Should ACCEPT ---

    # 9. Valid simple query
    r = reviewer.review(
        "SELECT * FROM memory WHERE user_id = 'test_user' "
        "AND tool = 'gmail' LIMIT 20",
        user_id
    )
    check("Accepts valid simple query", r["approved"] is True)

    # 10. Valid aggregation with json_extract
    r = reviewer.review(
        "SELECT json_extract(content,'$.from'), COUNT(*) FROM memory "
        "WHERE user_id='test_user' GROUP BY 1",
        user_id
    )
    check("Accepts valid aggregation query", r["approved"] is True)

    # 11. Unknown json_extract path should be rejected
    r = reviewer.review(
        "SELECT json_extract(content,'$.password') FROM memory "
        "WHERE user_id='test_user' LIMIT 10",
        user_id
    )
    check("Rejects unknown json_extract path ($.password)",
          r["approved"] is False)

    # =================================================================
    # TEST GROUP 2: Integration with seeded data (needs GROQ_API_KEY)
    # =================================================================
    print("\n--- 2. Integration with Seeded Data ---")

    from dotenv import load_dotenv
    load_dotenv()

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        print("  [SKIP] GROQ_API_KEY not set — skipping LLM integration tests")
        print("\n--- 3. Full Pipeline ---")
        print("  [SKIP] GROQ_API_KEY not set — skipping pipeline tests")
    else:
        from database.sqlite_manager import DatabaseManager
        from database.vector_store import VectorStore
        from tools.local_tools import LocalToolManager
        from core.sql_generator import SQLGenerator, get_sql_generator
        import core.sql_generator as sg_module

        _test_dir = os.path.dirname(os.path.abspath(__file__))
        TEST_DB = os.path.join(_test_dir, "test_phase5.db")
        TEST_CHROMA = os.path.join(_test_dir, "test_phase5_chroma")

        # Clean previous
        for f in [TEST_DB]:
            if os.path.exists(f):
                os.remove(f)
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

        # Seed test data — 5 emails + 3 slack messages
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        emails = [
            {"id": "m1", "from": "john@co.com", "subject": "Budget Report",
             "body": "Here is the Q4 budget analysis.",
             "date": today, "unread": True},
            {"id": "m2", "from": "john@co.com", "subject": "Lunch?",
             "body": "Want to grab lunch today?",
             "date": yesterday, "unread": False},
            {"id": "m3", "from": "boss@co.com", "subject": "Meeting moved",
             "body": "The 3pm meeting is moved to 4pm.",
             "date": today, "unread": True},
            {"id": "m4", "from": "hr@co.com", "subject": "John promoted",
             "body": "Please congratulate John on his promotion.",
             "date": "2026-02-15", "unread": False},
            {"id": "m5", "from": "sarah@co.com", "subject": "Budget Q",
             "body": "Quick question about the budget spreadsheet.",
             "date": today, "unread": True},
        ]
        slack_msgs = [
            {"id": "s1", "channel": "#general", "user": "alice",
             "text": "Deployment done!", "ts": "1708300000.000100",
             "reactions": ["thumbsup"]},
            {"id": "s2", "channel": "#dev", "user": "bob",
             "text": "Found a bug in login page", "ts": "1708300100.000200",
             "reactions": []},
            {"id": "s3", "channel": "#general", "user": "john",
             "text": "Great work everyone!", "ts": "1708300200.000300",
             "reactions": ["heart", "fire"]},
        ]
        await lt.cache_results("test_user", "gmail", "email", emails)
        await lt.cache_results("test_user", "slack", "message", slack_msgs)

        # Test: generate_and_execute returns proper format
        sg = get_sql_generator()

        # Test "emails from john" — should use json_extract on $.from
        result = await sg.generate_and_execute(
            "emails from john", "test_user", "gmail", db
        )
        if result is not None:
            llm_results = result.get("results", [])
            check("'emails from john': returns results",
                  len(llm_results) > 0)
            check("'emails from john': source is llm_sql",
                  result.get("source") == "llm_sql")
            check("'emails from john': sql field present",
                  "sql" in result)

            # Precision check: should match m1, m2 (from john@co.com)
            # but NOT m4 (which only mentions John in subject/body)
            from_johns = [r for r in llm_results
                          if isinstance(r, dict)
                          and "john" in r.get("from", "").lower()]
            check("'emails from john': precise $.from matching (>= 2 from john)",
                  len(from_johns) >= 2)
        else:
            check("'emails from john': LLM returned result (got None — "
                  "LLM may have generated bad SQL)", False)

        # Test "unread emails"
        result2 = await sg.generate_and_execute(
            "unread emails", "test_user", "gmail", db
        )
        if result2 is not None:
            unread_results = result2.get("results", [])
            check("'unread emails': returns results",
                  len(unread_results) > 0)
            # Should match m1, m3, m5 (unread: true)
            unread_count = len([r for r in unread_results
                                if isinstance(r, dict)
                                and r.get("unread") is True])
            check("'unread emails': found unread items",
                  unread_count > 0)
        else:
            check("'unread emails': LLM returned result (got None)", False)

        # Test "emails about budget"
        result3 = await sg.generate_and_execute(
            "emails about budget", "test_user", "gmail", db
        )
        if result3 is not None:
            budget_results = result3.get("results", [])
            check("'emails about budget': returns results",
                  len(budget_results) > 0)
        else:
            check("'emails about budget': LLM returned result (got None)",
                  False)

        # =============================================================
        # TEST GROUP 3: Date Queries
        # =============================================================
        print("\n--- 3. Date Queries ---")

        # "emails from today" — should resolve to actual date
        r_today = await sg.generate_and_execute(
            "emails from today", "test_user", "gmail", db
        )
        if r_today is not None:
            today_results = r_today.get("results", [])
            check(f"'emails from today': returns results (today={today})",
                  len(today_results) > 0)
            # m1, m3, m5 have today's date
            today_dates = [r for r in today_results
                           if isinstance(r, dict)
                           and r.get("date") == today]
            check("'emails from today': results have today's date",
                  len(today_dates) >= 2)
            # Verify the SQL contains the actual date, not the word "today"
            check("'emails from today': SQL uses actual date not 'today'",
                  today in r_today.get("sql", ""))
        else:
            check("'emails from today': LLM returned result", False)

        # "emails from yesterday" — simpler phrasing to avoid LLM adding unread filter
        r_yesterday = await sg.generate_and_execute(
            "emails from yesterday", "test_user", "gmail", db
        )
        if r_yesterday is not None:
            yday_sql = r_yesterday.get("sql", "")
            check("'emails from yesterday': SQL uses actual date",
                  yesterday in yday_sql)
            yday_results = r_yesterday.get("results", [])
            check(f"'emails from yesterday': returns results (yesterday={yesterday})",
                  len(yday_results) > 0)
        else:
            check("'emails from yesterday': LLM returned result", False)

        # =============================================================
        # TEST GROUP 4: Slack $.user field
        # =============================================================
        print("\n--- 4. Slack $.user ---")

        # "messages from bob" in slack — should use $.user not $.from
        r_bob = await sg.generate_and_execute(
            "messages from bob", "test_user", "slack", db
        )
        if r_bob is not None:
            bob_results = r_bob.get("results", [])
            check("'messages from bob': returns results",
                  len(bob_results) > 0)
            bob_users = [r for r in bob_results
                         if isinstance(r, dict)
                         and r.get("user") == "bob"]
            check("'messages from bob': matched via $.user",
                  len(bob_users) >= 1)
        else:
            check("'messages from bob': LLM returned result", False)

        # =============================================================
        # TEST GROUP 5: Multi-Agent Queries
        # =============================================================
        print("\n--- 5. Multi-Agent Queries ---")

        # "check gmail and slack for anything from john"
        r_multi = await sg.generate_and_execute(
            "check gmail and slack for anything from john",
            "test_user", None, db
        )
        if r_multi is not None:
            multi_results = r_multi.get("results", [])
            check("'gmail and slack from john': returns results",
                  len(multi_results) > 0)
            # Should find gmail emails (m1, m2) AND slack msg (s3)
            has_gmail = any(isinstance(r, dict) and r.get("from", "")
                           for r in multi_results)
            has_slack = any(isinstance(r, dict) and r.get("channel", "")
                           for r in multi_results)
            check("'gmail and slack from john': has gmail results",
                  has_gmail)
            check("'gmail and slack from john': has slack results",
                  has_slack)
        else:
            check("'gmail and slack from john': LLM returned result", False)

        # "emails from john and slack messages in #general"
        r_multi2 = await sg.generate_and_execute(
            "emails from john and slack messages in #general",
            "test_user", None, db
        )
        if r_multi2 is not None:
            multi2_results = r_multi2.get("results", [])
            check("'john emails + #general slack': returns results",
                  len(multi2_results) > 0)
            check("'john emails + #general slack': mixed tool results",
                  len(multi2_results) >= 3)
        else:
            check("'john emails + #general slack': LLM returned result",
                  False)

        # =============================================================
        # TEST GROUP 6: Aggregation Queries
        # =============================================================
        print("\n--- 6. Aggregation Queries ---")

        # "who sends me the most emails" — GROUP BY + COUNT
        r_agg = await sg.generate_and_execute(
            "who sends me the most emails", "test_user", "gmail", db
        )
        if r_agg is not None:
            agg_results = r_agg.get("results", [])
            check("'who sends most emails': returns results",
                  len(agg_results) > 0)
            # Results should be dicts with actual data, not empty {}
            non_empty = [r for r in agg_results
                         if isinstance(r, dict) and len(r) > 0]
            check("'who sends most emails': results are NOT empty dicts",
                  len(non_empty) > 0)
            # john@co.com should have count >= 2
            john_row = [r for r in non_empty
                        if "john" in str(r).lower()]
            check("'who sends most emails': john@co.com appears in results",
                  len(john_row) > 0)
        else:
            check("'who sends most emails': LLM returned result", False)

        # "how many emails per sender"
        r_agg2 = await sg.generate_and_execute(
            "how many emails per sender", "test_user", "gmail", db
        )
        if r_agg2 is not None:
            agg2_results = r_agg2.get("results", [])
            check("'emails per sender': returns results",
                  len(agg2_results) > 0)
            non_empty2 = [r for r in agg2_results
                          if isinstance(r, dict) and len(r) > 0]
            check("'emails per sender': results have actual data",
                  len(non_empty2) > 0)
        else:
            check("'emails per sender': LLM returned result", False)

        # =============================================================
        # TEST GROUP 7: Full Pipeline (search_local with LLM SQL layer)
        # =============================================================
        print("\n--- 7. Full Pipeline ---")

        # Monkey-patch local_tools singleton
        import tools.local_tools as lt_module
        original_lt = lt_module._local_tools_instance
        lt_module._local_tools_instance = lt

        try:
            # Test: search_local uses LLM SQL and returns results
            search_result = await lt.search_local(
                "test_user", "gmail", "emails from john"
            )
            check("search_local returns results for 'emails from john'",
                  len(search_result.get("results", [])) > 0)

            # If LLM SQL worked, source should be llm_sql
            src = search_result.get("source", "")
            check(f"search_local source is 'llm_sql' (got '{src}')",
                  src == "llm_sql")

            # Test: fallback — if LLM SQL returns None, falls through to LIKE
            # Simulate by temporarily breaking the sql generator
            original_sg = sg_module._generator_instance

            class BrokenGenerator:
                async def generate_and_execute(self, *args, **kwargs):
                    return None

            sg_module._generator_instance = BrokenGenerator()

            fallback_result = await lt.search_local(
                "test_user", "gmail", "john"
            )
            check("Fallback: LIKE search still works when LLM SQL fails",
                  len(fallback_result.get("results", [])) > 0)
            check("Fallback: source is NOT llm_sql",
                  fallback_result.get("source") != "llm_sql")

            # Restore
            sg_module._generator_instance = original_sg

            # Test: no query → skips LLM SQL, goes straight to LIKE
            no_query_result = await lt.search_local(
                "test_user", "gmail"
            )
            check("No query: skips LLM SQL, returns cached data",
                  len(no_query_result.get("results", [])) > 0)
            check("No query: source is sqlite (threshold met)",
                  no_query_result.get("source") in ("sqlite", "hybrid"))

        finally:
            lt_module._local_tools_instance = original_lt

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
            print("  [INFO] Could not delete test ChromaDB "
                  "(locked, OK on Windows)")

    # =================================================================
    # SUMMARY
    # =================================================================
    print()
    print("=" * 60)
    print(f"[RESULTS] {passed} passed, {failed} failed "
          f"out of {passed + failed} tests")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"WARNING: {failed} test(s) failed")


if __name__ == "__main__":
    asyncio.run(run_tests())

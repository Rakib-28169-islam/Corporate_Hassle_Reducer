"""
=============================================================================
PHASE 5 VISUAL TEST — See exactly what SQL the LLM generates
=============================================================================

Shows the full pipeline for each query:
  Query → Generated SQL → Reviewer verdict → Results

Run with:
    cd backend
    python test/test_phase5_sql_visual.py

Or pass your own query:
    python test/test_phase5_sql_visual.py "emails from john about budget"
=============================================================================
"""

import sys
import os
import asyncio
import json
import shutil

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _backend_dir)
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()


# --- Pretty printing helpers ---

def header(text):
    print(f"\n{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}")


def section(label, value):
    print(f"  {label:18s}: {value}")


def sql_box(sql):
    if not sql:
        print("  SQL: (none generated)")
        return
    print(f"  {'─' * 66}")
    for line in sql.strip().split("\n"):
        print(f"  | {line}")
    print(f"  {'─' * 66}")


async def run_visual_test(custom_queries=None):
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        print("[ERROR] GROQ_API_KEY not set in .env — needed for LLM SQL generation")
        return

    from database.sqlite_manager import DatabaseManager
    from database.vector_store import VectorStore
    from tools.local_tools import LocalToolManager
    from core.sql_generator import SQLGenerator, SQLReviewer

    _test_dir = os.path.dirname(os.path.abspath(__file__))
    TEST_DB = os.path.join(_test_dir, "test_phase5_visual.db")
    TEST_CHROMA = os.path.join(_test_dir, "test_phase5_visual_chroma")

    # Clean previous
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    try:
        if os.path.exists(TEST_CHROMA):
            shutil.rmtree(TEST_CHROMA)
    except PermissionError:
        pass

    # Setup
    db = DatabaseManager(db_path=TEST_DB)
    await db.init_db()
    vs = VectorStore(persist_dir=TEST_CHROMA)
    lt = LocalToolManager()
    lt.db = db
    lt.vs = vs

    # Seed test data
    emails = [
        {"id": "m1", "from": "john@co.com", "to": "me@co.com",
         "subject": "Budget Report", "body": "Here is the Q4 budget analysis.",
         "date": "2026-02-18", "unread": True, "labels": ["important"],
         "has_attachment": True},
        {"id": "m2", "from": "john@co.com", "to": "me@co.com",
         "subject": "Lunch?", "body": "Want to grab lunch today?",
         "date": "2026-02-17", "unread": False, "labels": [],
         "has_attachment": False},
        {"id": "m3", "from": "boss@co.com", "to": "me@co.com",
         "subject": "Meeting moved", "body": "The 3pm meeting is moved to 4pm.",
         "date": "2026-02-18", "unread": True, "labels": ["work"],
         "has_attachment": False},
        {"id": "m4", "from": "hr@co.com", "to": "team@co.com",
         "subject": "John promoted", "body": "Please congratulate John.",
         "date": "2026-02-15", "unread": False, "labels": ["hr"],
         "has_attachment": False},
        {"id": "m5", "from": "sarah@co.com", "to": "me@co.com",
         "subject": "Budget Q", "body": "Quick question about the budget.",
         "date": "2026-02-18", "unread": True, "labels": ["important"],
         "has_attachment": True},
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

    header("SEEDED TEST DATA")
    print(f"\n  Gmail emails ({len(emails)}):")
    for e in emails:
        flag = " [UNREAD]" if e.get("unread") else ""
        att = " [ATTACH]" if e.get("has_attachment") else ""
        print(f"    {e['id']}  from={e['from']:18s}  subj={e['subject']:20s}"
              f"  date={e['date']}{flag}{att}")
    print(f"\n  Slack messages ({len(slack_msgs)}):")
    for s in slack_msgs:
        print(f"    {s['id']}  {s['channel']:10s}  user={s['user']:8s}"
              f"  text={s['text'][:40]}")

    # Default test queries
    test_queries = [
        # Gmail - field precision
        ("emails from john",                    "gmail"),
        ("unread emails",                       "gmail"),
        ("emails about budget",                 "gmail"),
        ("emails with attachments",             "gmail"),
        ("emails from boss today",              "gmail"),
        # Gmail - aggregation / counting
        ("how many unread emails do I have",    "gmail"),
        ("who sends me the most emails",        "gmail"),
        # Slack
        ("messages in #general",                "slack"),
        ("messages from bob",                   "slack"),
        # Cross-tool (no tool specified)
        ("anything about deployment",           None),
        # Edge cases
        ("show me everything",                  "gmail"),
    ]

    # Add custom queries from CLI args
    if custom_queries:
        for q in custom_queries:
            test_queries.append((q, None))

    sg = SQLGenerator()
    reviewer = SQLReviewer()
    user_id = "test_user"

    header("SQL GENERATION RESULTS")

    for i, (query, tool) in enumerate(test_queries, 1):
        print(f"\n  [{i}/{len(test_queries)}] Query: \"{query}\""
              f"  (tool={tool or 'auto'})")

        # Step 1: Generate SQL
        sql = sg._generate_sql(query, user_id, tool)
        sql_box(sql)

        if not sql:
            section("Status", "FAILED — LLM returned no SQL")
            continue

        # Step 2: Review
        review = reviewer.review(sql, user_id)
        if review["approved"]:
            section("Reviewer", "APPROVED")
        else:
            section("Reviewer", f"REJECTED — {review['reason']}")
            continue

        # Step 3: Execute
        try:
            rows = await db.execute_sql(sql)
            section("Results", f"{len(rows)} row(s)")

            # Show first 3 results briefly
            for j, row in enumerate(rows[:3]):
                content = row.get("content", "{}")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if isinstance(content, dict):
                    # Show a compact summary
                    brief = {k: v for k, v in list(content.items())[:4]}
                    print(f"    [{j+1}] {brief}")
                else:
                    print(f"    [{j+1}] {str(content)[:80]}")
            if len(rows) > 3:
                print(f"    ... and {len(rows) - 3} more")

        except Exception as e:
            section("Execute", f"ERROR — {e}")

    # Cleanup
    print()
    header("DONE")
    try:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
    except PermissionError:
        pass
    try:
        if os.path.exists(TEST_CHROMA):
            shutil.rmtree(TEST_CHROMA)
    except PermissionError:
        print("  [INFO] ChromaDB locked (OK on Windows)")


if __name__ == "__main__":
    # Accept custom queries from command line
    custom = sys.argv[1:] if len(sys.argv) > 1 else None
    asyncio.run(run_visual_test(custom))

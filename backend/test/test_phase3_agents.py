"""
=============================================================================
PHASE 3 — End-to-End Test for BaseAgent + All Specialist Agents
=============================================================================

Tests the full pipeline:
    1. Operation Classifier -> detect operations
    2. BaseAgent.execute() -> run pipeline
    3. GmailAgent / SlackAgent / OutlookAgent -> tool-specific overrides
    4. LocalToolManager -> smart threshold search
    5. SyncService -> cache invalidation after actions

Run: python test/test_phase3_agents.py
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
TEST_DB = os.path.join(_test_dir, "test_phase3.db")
TEST_CHROMA = os.path.join(_test_dir, "test_phase3_chroma")


async def run_tests():
    print("Phase 3: End-to-End Agent Tests")
    print("=" * 60)

    from database.sqlite_manager import DatabaseManager
    from database.vector_store import VectorStore
    from tools.local_tools import LocalToolManager
    from services.sync_service import SyncService
    from agents.gmail_agent import GmailAgent
    from agents.slack_agent import SlackAgent
    from agents.outlook_agent import OutlookAgent
    from core.operation_classifier import get_classifier

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

    sync_svc = SyncService()
    sync_svc.local_tools = lt

    # Helper to inject test DB into any agent
    def setup_agent(agent):
        agent.local_tools = lt
        agent.sync = sync_svc
        return agent

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
    # SEED DATA — Simulate cached emails and messages
    # =================================================================

    gmail_emails = [
        {"id": "gmail_001", "subject": "Invoice #1001",
         "from": "billing@vendor.com", "body": "Amount due: $500"},
        {"id": "gmail_002", "subject": "Team standup notes",
         "from": "john@company.com", "body": "Discussed deployment plan"},
        {"id": "gmail_003", "subject": "Meeting tomorrow",
         "from": "boss@company.com", "body": "Agenda: Q1 review at 3pm"},
        {"id": "gmail_004", "subject": "Project update",
         "from": "sarah@company.com", "body": "Frontend v2 deployed"},
        {"id": "gmail_005", "subject": "Quarterly report",
         "from": "hr@company.com", "body": "Revenue: $10000"},
    ]
    await lt.cache_results("test_user", "gmail", "email", gmail_emails)

    slack_messages = [
        {"id": "slack_001", "text": "Deploy at 5pm today",
         "channel": "#dev", "from": "john"},
        {"id": "slack_002", "text": "Bug found in login page",
         "channel": "#bugs", "from": "sarah"},
        {"id": "slack_003", "text": "Sprint review tomorrow",
         "channel": "#general", "from": "boss"},
        {"id": "slack_004", "text": "New API docs ready",
         "channel": "#dev", "from": "dev-bot"},
    ]
    await lt.cache_results("test_user", "slack", "message", slack_messages)

    outlook_events = [
        {"id": "evt_001", "subject": "Team standup",
         "start": "2026-02-19 09:00", "end": "2026-02-19 09:30"},
        {"id": "evt_002", "subject": "1-on-1 with boss",
         "start": "2026-02-19 14:00", "end": "2026-02-19 14:30"},
        {"id": "evt_003", "subject": "Sprint planning",
         "start": "2026-02-20 10:00", "end": "2026-02-20 11:00"},
    ]
    await lt.cache_results("test_user", "outlook", "event", outlook_events)

    # =================================================================
    # TEST 1: GmailAgent — Search (cached, threshold met)
    # =================================================================
    print("\n--- GmailAgent Tests ---")
    gmail = setup_agent(GmailAgent(user_id="test_user"))

    result = await gmail.execute("show my emails")
    check("Gmail SEARCH: found results",
          result["results"].get("SEARCH", {}).get("count", 0) > 0)
    check("Gmail SEARCH: source is local",
          result["results"].get("SEARCH", {}).get("source", "") in
          ["sqlite", "chromadb", "hybrid"])
    check("Gmail execute returns final_answer",
          len(result.get("final_answer", "")) > 0)

    # =================================================================
    # TEST 2: GmailAgent — Search + Calculate
    # =================================================================
    result2 = await gmail.execute("how many emails from john?",
                                  operations=["SEARCH", "CALCULATE"])
    check("Gmail SEARCH+CALC: has both results",
          "SEARCH" in result2["results"] and "CALCULATE" in result2["results"])
    calc = result2["results"].get("CALCULATE", {})
    check(f"Gmail CALCULATE: Python method, answer={calc.get('answer')}",
          calc.get("method") == "python")

    # =================================================================
    # TEST 3: GmailAgent — Action (returns need_details)
    # =================================================================
    result3 = await gmail.execute("send email to boss",
                                  operations=["ACTION"])
    action = result3["results"].get("ACTION", {})
    check("Gmail ACTION: returns need_details",
          action.get("status") == "need_details")

    # =================================================================
    # TEST 4: SlackAgent — Search
    # =================================================================
    print("\n--- SlackAgent Tests ---")
    slack = setup_agent(SlackAgent(user_id="test_user"))

    result4 = await slack.execute("search slack messages")
    check("Slack SEARCH: found results",
          result4["results"].get("SEARCH", {}).get("count", 0) > 0)

    # =================================================================
    # TEST 5: SlackAgent — Search + Summarize
    # =================================================================
    result5 = await slack.execute("summarize recent slack messages",
                                  operations=["SEARCH", "SUMMARIZE"])
    check("Slack SEARCH+SUMMARIZE: has both results",
          "SEARCH" in result5["results"] and "SUMMARIZE" in result5["results"])
    summ = result5["results"].get("SUMMARIZE", {})
    check("Slack SUMMARIZE: generated summary",
          len(summ.get("summary", "")) > 0)

    # =================================================================
    # TEST 6: SlackAgent — Action
    # =================================================================
    result6 = await slack.execute("send message to #general",
                                  operations=["ACTION"])
    check("Slack ACTION: returns need_details",
          result6["results"].get("ACTION", {}).get("status") == "need_details")

    # =================================================================
    # TEST 7: OutlookAgent — Search
    # =================================================================
    print("\n--- OutlookAgent Tests ---")
    outlook = setup_agent(OutlookAgent(user_id="test_user"))

    result7 = await outlook.execute("show my calendar events")
    check("Outlook SEARCH: found results",
          result7["results"].get("SEARCH", {}).get("count", 0) > 0)

    # =================================================================
    # TEST 8: OutlookAgent — Action
    # =================================================================
    result8 = await outlook.execute("schedule a meeting tomorrow",
                                    operations=["ACTION"])
    check("Outlook ACTION: returns need_details for create_event",
          result8["results"].get("ACTION", {}).get("action") == "create_event")

    # =================================================================
    # TEST 9: Chat (any agent, no tool needed)
    # =================================================================
    print("\n--- Chat Tests ---")
    result9 = await gmail.execute("hello how are you",
                                  operations=["CHAT"])
    check("CHAT: returns a response",
          len(result9["results"].get("CHAT", {}).get("response", "")) > 0)

    # =================================================================
    # TEST 10: Compose (LLM drafts content)
    # =================================================================
    print("\n--- Compose Tests ---")
    result10 = await gmail.execute(
        "draft a professional reply about the meeting",
        operations=["COMPOSE"]
    )
    compose = result10["results"].get("COMPOSE", {})
    check("COMPOSE: generated content",
          compose.get("content") is not None and len(compose.get("content", "")) > 0)

    # =================================================================
    # TEST 11: Invalidation after action
    # =================================================================
    print("\n--- Invalidation Tests ---")
    gmail_count_before = await lt.get_local_count("test_user", "gmail")
    check(f"Gmail has {gmail_count_before['sqlite_count']} items before invalidation",
          gmail_count_before["sqlite_count"] == 5)

    await gmail._notify_action_complete("SEND_EMAIL")
    gmail_count_after = await lt.get_local_count("test_user", "gmail")
    check("Gmail cache cleared after action notification",
          gmail_count_after["sqlite_count"] == 0)

    # Slack should be unaffected
    slack_count = await lt.get_local_count("test_user", "slack")
    check("Slack cache NOT affected by Gmail invalidation",
          slack_count["sqlite_count"] == 4)

    # =================================================================
    # TEST 12: Agent inheritance checks
    # =================================================================
    print("\n--- Inheritance Tests ---")
    from agents.base_agent import BaseAgent
    check("GmailAgent inherits BaseAgent",
          isinstance(gmail, BaseAgent))
    check("SlackAgent inherits BaseAgent",
          isinstance(slack, BaseAgent))
    check("OutlookAgent inherits BaseAgent",
          isinstance(outlook, BaseAgent))

    check("GmailAgent.tool_name = 'gmail'", gmail.tool_name == "gmail")
    check("SlackAgent.tool_name = 'slack'", slack.tool_name == "slack")
    check("OutlookAgent.tool_name = 'outlook'", outlook.tool_name == "outlook")

    check("GmailAgent._get_data_type() = 'email'",
          gmail._get_data_type() == "email")
    check("SlackAgent._get_data_type() = 'message'",
          slack._get_data_type() == "message")
    check("OutlookAgent._get_data_type() = 'email'",
          outlook._get_data_type() == "email")

    # =================================================================
    # TEST 13: Full pipeline — classify + execute
    # =================================================================
    print("\n--- Full Pipeline Test ---")
    classifier = get_classifier()

    # Simulate what SupervisorAgent will do:
    query = "how many emails do I have?"
    classification = classifier.classify(query)
    check(f"Classifier: '{query}' -> {classification['operations']}",
          "SEARCH" in classification["operations"]
          and "CALCULATE" in classification["operations"])

    # Re-seed gmail (was invalidated in test 11)
    await lt.cache_results("test_user", "gmail", "email", gmail_emails)

    full_result = await gmail.execute(query, classification["operations"])
    check("Full pipeline: returns final_answer",
          len(full_result.get("final_answer", "")) > 0)
    check("Full pipeline: SEARCH + CALCULATE both executed",
          "SEARCH" in full_result["results"]
          and "CALCULATE" in full_result["results"])

    # =================================================================
    # PHASE 5B — Smart Calculate Pipeline Tests
    # =================================================================
    print("\n--- Phase 5B: Smart Calculate Pipeline ---")

    # Re-seed gmail for remaining tests (may have been cleared)
    await lt.cache_results("test_user", "gmail", "email", gmail_emails)

    # ----- Test 14: _extract_currency finds money in body text -----
    currency_data = [
        {"subject": "Invoice", "body": "Total: $500.00 due by Friday"}
    ]
    hits = gmail._extract_currency(currency_data)
    check("_extract_currency finds $500.00 in body",
          len(hits) == 1 and hits[0]["amount"] == 500.0
          and hits[0]["source"] == "Invoice")

    # ----- Test 15: _extract_currency ignores bare numbers -----
    bare_data = [
        {"subject": "Report 2024",
         "body": "We have 150 employees in building 4200"}
    ]
    hits2 = gmail._extract_currency(bare_data)
    check("_extract_currency ignores bare numbers (no currency context)",
          len(hits2) == 0)

    # ----- Test 16: _extract_currency handles multiple currencies -----
    multi_data = [
        {"subject": "Expenses",
         "body": "Hotel $200, Meals €50, Transport 1000 BDT"}
    ]
    hits3 = gmail._extract_currency(multi_data)
    check(f"_extract_currency finds 3 currency amounts (got {len(hits3)})",
          len(hits3) == 3)

    # ----- Test 17: _is_aggregation_result detects aggregation -----
    check("_is_aggregation_result: [sender, count] -> True",
          gmail._is_aggregation_result(
              [{"sender": "john@co.com", "count": 5}]) is True)
    check("_is_aggregation_result: [from, subject] -> False",
          gmail._is_aggregation_result(
              [{"from": "john@co.com", "subject": "Hi"}]) is False)
    check("_is_aggregation_result: empty list -> False",
          gmail._is_aggregation_result([]) is False)

    # ----- Test 18: _do_calculate aggregation passthrough -----
    agg_context = {
        "search_results": [
            {"sender": "j@co", "count": 5},
            {"sender": "b@co", "count": 3},
        ],
        "search_source": "llm_sql",
    }
    agg_result = await gmail._do_calculate(
        "who sends me the most emails", agg_context
    )
    check(f"Aggregation passthrough: answer contains 'j@co' and '5' "
          f"(got: {agg_result['answer']})",
          "j@co" in agg_result["answer"] and "5" in agg_result["answer"]
          and agg_result["method"] == "python")

    # ----- Test 19: _do_calculate with currency sum -----
    currency_context = {
        "search_results": [
            {"subject": "Invoice A", "body": "Amount: $500"},
            {"subject": "Invoice B", "body": "Amount: $250"},
        ],
    }
    currency_result = await gmail._do_calculate(
        "total amount of invoices", currency_context
    )
    check(f"Currency sum: value={currency_result['value']} (expected 750.0)",
          currency_result["value"] == 750.0
          and currency_result["method"] == "python")

    # ----- Test 20: _do_calculate count still works -----
    count_ctx = {"search_results": gmail_emails}
    count_result = await gmail._do_calculate("how many emails?", count_ctx)
    check(f"Count still works: {count_result['value']} (expected 5)",
          count_result["value"] == 5 and count_result["method"] == "python")

    # ----- Test 21: search_source passed through context -----
    exec_result2 = await gmail.execute("how many emails?",
                                       operations=["SEARCH", "CALCULATE"])
    # The search_source should have been set during execute
    search_src = exec_result2["results"].get("SEARCH", {}).get("source", "")
    check(f"execute() passes search_source (got: '{search_src}')",
          search_src in ["sqlite", "chromadb", "hybrid", "composio", "llm_sql"])

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

"""Inline tests extracted from operation_classifier.py."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from core.operation_classifier import OperationClassifier

if __name__ == "__main__":
    print("Testing OperationClassifier...")
    print("=" * 60)

    c = OperationClassifier()

    test_queries = [
        # 1. Pure SEARCH
        ("show my unread emails", ["SEARCH"]),
        # 2. SEARCH + CALCULATE (count needs data first)
        ("how many emails from John?", ["CALCULATE", "SEARCH"]),
        # 3. Pure ACTION
        ("send email to boss", ["ACTION"]),
        # 4. SEARCH + SUMMARIZE (summarize needs data first)
        ("summarize slack #general", ["SEARCH", "SUMMARIZE"]),
        # 5. Pure CHAT (greeting)
        ("hello", ["CHAT"]),
        # 6. COMPOSE + ACTION (draft a reply — "reply" is an action word)
        ("draft a reply to the last email", ["COMPOSE", "ACTION"]),
        # 7. SEARCH + CALCULATE (total)
        ("what is the total amount from invoices?", ["CALCULATE", "SEARCH"]),
        # 8. Pure SEARCH with filter
        ("find emails about deployment since last week", ["SEARCH"]),
        # 9. ACTION (calendar)
        ("schedule a meeting tomorrow at 3pm", ["ACTION"]),
        # 10. SUMMARIZE + SEARCH
        ("give me a recap of today's messages", ["SEARCH", "SUMMARIZE"]),
        # 11. COMPOSE + ACTION (draft and send)
        ("write and send an email to john about the meeting", ["ACTION", "COMPOSE"]),
        # 12. CHAT (help)
        ("what can you do", ["CHAT"]),
        # 13. SEARCH with multiple signals
        ("show me the latest messages from #dev channel", ["SEARCH"]),
        # 14. CALCULATE with compare
        ("compare emails from john vs sarah", ["CALCULATE", "SEARCH"]),
    ]

    passed = 0
    failed = 0

    for query, expected_ops in test_queries:
        result = c.classify(query)
        actual_ops = result["operations"]

        match = set(expected_ops) == set(actual_ops)
        status = "[OK]" if match else "[MISS]"

        if match:
            passed += 1
        else:
            failed += 1

        print(f"  {status} \"{query}\"")
        print(f"        Expected: {expected_ops}")
        print(f"        Got:      {actual_ops}")
        if result["pipeline"]:
            steps = ", ".join(
                f"step{p['step']}:{p['op']}" for p in result["pipeline"]
            )
            print(f"        Pipeline: {steps}")
        print()

    # Test dependency pipeline specifically
    print("-" * 60)
    print("Pipeline dependency tests:")
    print()

    # SEARCH+CALCULATE → SEARCH must be step 1, CALCULATE step 2
    r = c.classify("how many emails from john?")
    search_step = next(p for p in r["pipeline"] if p["op"] == "SEARCH")["step"]
    calc_step = next(p for p in r["pipeline"] if p["op"] == "CALCULATE")["step"]
    dep_ok = search_step < calc_step
    print(f"  {'[OK]' if dep_ok else '[FAIL]'} SEARCH(step {search_step}) before CALCULATE(step {calc_step})")

    # SEARCH+SUMMARIZE → SEARCH must be step 1, SUMMARIZE step 2
    r = c.classify("summarize my inbox")
    search_step = next(p for p in r["pipeline"] if p["op"] == "SEARCH")["step"]
    summ_step = next(p for p in r["pipeline"] if p["op"] == "SUMMARIZE")["step"]
    dep_ok2 = search_step < summ_step
    print(f"  {'[OK]' if dep_ok2 else '[FAIL]'} SEARCH(step {search_step}) before SUMMARIZE(step {summ_step})")

    # Data source test
    print()
    print("-" * 60)
    print("Data source strategies:")
    print()
    for op in ["SEARCH", "CALCULATE", "ACTION", "COMPOSE", "SUMMARIZE", "CHAT"]:
        ds = c.get_data_source(op)
        print(f"  {op:12} -> source: {ds['source']:10} fallback: {ds['fallback']}  cache: {ds['cache']}")

    print()
    print("=" * 60)
    print(f"[RESULTS] {passed} passed, {failed} missed out of {len(test_queries)} tests")

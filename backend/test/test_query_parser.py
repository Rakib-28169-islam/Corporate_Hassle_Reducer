"""Tests for Phase 7: QueryParser, IntentValidator, deterministic SQL, formatter."""

import sys
import os
import json
import asyncio
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.query_parser import (
    ParsedIntent, IntentValidator, QueryParser, parse_date_phrase,
    VALID_INTENTS, VALID_QUERY_TYPES, VALID_FILTER_KEYS,
)
from core.sql_generator import SQLReviewer, SQLGenerator
from agents.base_agent import BaseAgent

passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}")
        failed += 1


# ============================================================
# Group 1: parse_date_phrase (deterministic, no API)
# ============================================================
print("\n=== Group 1: Deterministic Date Parser ===")

today = date(2026, 2, 21)

check("today → 2026-02-21",
      parse_date_phrase("today", today) == "2026-02-21")

check("yesterday → 2026-02-20",
      parse_date_phrase("yesterday", today) == "2026-02-20")

check("last 3 days → 2026-02-18",
      parse_date_phrase("last 3 days", today) == "2026-02-18")

check("this week → 2026-02-16 (Monday)",
      parse_date_phrase("this week", today) == "2026-02-16")

check("last week → 2026-02-09 (prev Monday)",
      parse_date_phrase("last week", today) == "2026-02-09")

check("March 5 → 2025-03-05 (past, not future)",
      parse_date_phrase("March 5", today) == "2025-03-05")

check("5 March → 2025-03-05",
      parse_date_phrase("5 March", today) == "2025-03-05")

check("January 15 → 2026-01-15 (already passed this year)",
      parse_date_phrase("January 15", today) == "2026-01-15")

check("Feb 14 → 2026-02-14",
      parse_date_phrase("Feb 14", today) == "2026-02-14")

check("already valid date passes through",
      parse_date_phrase("2026-03-01", today) == "2026-03-01")

check("None returns None",
      parse_date_phrase(None, today) is None)

check("empty string returns None",
      parse_date_phrase("", today) is None)

check("gibberish returns None",
      parse_date_phrase("xyzabc", today) is None)


# ============================================================
# Group 2: IntentValidator (no API)
# ============================================================
print("\n=== Group 2: IntentValidator ===")

validator = IntentValidator()

# Valid input
valid_raw = {
    "intent": "search",
    "limit": 10,
    "fields": ["subject", "from"],
    "sort": "date_desc",
    "filters": {"from": "alice", "unread": True},
    "query_type": "structured",
}
intent = validator.validate(valid_raw, tool="gmail")
check("valid intent parses correctly", intent.intent == "search")
check("valid limit preserved", intent.limit == 10)
check("valid fields preserved", intent.fields == ["subject", "from"])
check("valid filters preserved", intent.filters == {"from": "alice", "unread": True})
check("query_type: has exact filters → structured",
      intent.query_type == "structured")

# Limit clamping
intent_big = validator.validate({"limit": 999})
check("limit 999 clamped to 100", intent_big.limit == 100)

intent_neg = validator.validate({"limit": -5})
check("limit -5 clamped to 1", intent_neg.limit == 1)

intent_none = validator.validate({"limit": None})
check("limit None defaults to 20", intent_none.limit == 20)

intent_str = validator.validate({"limit": "abc"})
check("limit 'abc' defaults to 20", intent_str.limit == 20)

# Field whitelist
intent_bad_field = validator.validate(
    {"fields": ["subject", "hacked_field", "from"]}, tool="gmail"
)
check("unknown field filtered out",
      "hacked_field" not in intent_bad_field.fields and "subject" in intent_bad_field.fields)

# Filter whitelist
intent_bad_filter = validator.validate(
    {"filters": {"from": "alice", "hacked_key": "evil", "unread": True}}
)
check("unknown filter key dropped",
      "hacked_key" not in intent_bad_filter.filters and "from" in intent_bad_filter.filters)

# None filter values dropped
intent_none_filter = validator.validate(
    {"filters": {"from": "alice", "unread": None}}
)
check("None filter value dropped",
      "unread" not in intent_none_filter.filters)

# Query type override: backend rules override LLM
intent_override = validator.validate({
    "filters": {"from": "alice"},
    "query_type": "semantic",  # LLM says semantic
})
check("query_type override: exact filter → structured (not LLM's semantic)",
      intent_override.query_type == "structured")

intent_keyword = validator.validate({
    "filters": {"keyword": "marketing"},
    "query_type": "structured",  # LLM says structured
})
check("query_type override: keyword only → semantic (not LLM's structured)",
      intent_keyword.query_type == "semantic")

intent_hybrid = validator.validate({
    "filters": {"from": "alice", "keyword": "budget"},
    "query_type": "semantic",  # LLM says semantic
})
check("query_type override: filter + keyword → hybrid",
      intent_hybrid.query_type == "hybrid")

intent_count = validator.validate({"intent": "count"})
check("query_type override: count intent → structured",
      intent_count.query_type == "structured")

intent_no_filters = validator.validate({"filters": {}})
check("query_type: no filters → structured (recent items)",
      intent_no_filters.query_type == "structured")

# Date resolution
intent_date = validator.validate({
    "filters": {"date_phrase": "yesterday"},
})
check("date_phrase resolved to date_from/date_to",
      "date_from" in intent_date.filters and "date_phrase" not in intent_date.filters)

# Default intent
default = ParsedIntent.default()
check("default intent is search/semantic/20",
      default.intent == "search" and default.limit == 20 and default.parsed_by == "default")

# Invalid raw input
intent_empty = validator.validate({})
check("empty dict returns valid defaults",
      intent_empty.intent == "search" and intent_empty.limit == 20)

intent_nonsense = validator.validate("not a dict")
check("non-dict returns default",
      intent_nonsense.intent == "search")


# ============================================================
# Group 3: Deterministic SQL Builder (no API)
# ============================================================
print("\n=== Group 3: Deterministic SQL Builder ===")

from tools.local_tools import LocalToolManager

ltm = LocalToolManager.__new__(LocalToolManager)  # No init (no DB connection)

# Basic search
basic_intent = ParsedIntent(
    intent="search", limit=10, sort="date_desc",
    query_type="structured",
)
sql = ltm._build_intent_sql("user1", "gmail", basic_intent)
check("basic SQL has user_id", "user_id = 'user1'" in sql)
check("basic SQL has tool", "tool = 'gmail'" in sql)
check("basic SQL has LIMIT 10", "LIMIT 10" in sql)
check("basic SQL has ORDER BY DESC", "ORDER BY cached_at DESC" in sql)
check("basic SQL starts with SELECT content", sql.startswith("SELECT content"))

# With filters
filtered_intent = ParsedIntent(
    intent="search", limit=5, sort="date_desc",
    filters={"from": "alice", "unread": True},
    query_type="structured",
)
sql_filtered = ltm._build_intent_sql("user1", "gmail", filtered_intent)
check("filtered SQL has json_extract for from",
      "json_extract(content, '$.from') LIKE '%alice%'" in sql_filtered)
check("filtered SQL has json_extract for unread",
      "json_extract(content, '$.unread') = 1" in sql_filtered)
check("filtered SQL has LIMIT 5", "LIMIT 5" in sql_filtered)

# Date filter
date_intent = ParsedIntent(
    intent="search", limit=20,
    filters={"date_from": "2026-02-01", "date_to": "2026-02-28"},
    query_type="structured",
)
sql_date = ltm._build_intent_sql("user1", "gmail", date_intent)
check("date SQL has date_from condition",
      "json_extract(content, '$.date') >= '2026-02-01'" in sql_date)
check("date SQL has date_to condition",
      "json_extract(content, '$.date') <= '2026-02-28'" in sql_date)

# Count query
count_intent = ParsedIntent(
    intent="count", limit=100, filters={"unread": True},
    query_type="structured",
)
sql_count = ltm._build_intent_sql("user1", "gmail", count_intent)
check("count SQL has SELECT COUNT(*)",
      "SELECT COUNT(*) as count" in sql_count)

# Date ascending
asc_intent = ParsedIntent(sort="date_asc", query_type="structured")
sql_asc = ltm._build_intent_sql("user1", "gmail", asc_intent)
check("date_asc SQL has ORDER BY ASC", "ORDER BY cached_at ASC" in sql_asc)

# SQL safety: passes SQLReviewer
reviewer = SQLReviewer()
review = reviewer.review(sql_filtered, "user1")
check("filtered SQL passes safety review", review["approved"])

review_count = reviewer.review(sql_count, "user1")
check("count SQL passes safety review", review_count["approved"])

# Keyword filter
keyword_intent = ParsedIntent(
    filters={"keyword": "marketing"}, query_type="structured",
)
sql_kw = ltm._build_intent_sql("user1", "gmail", keyword_intent)
check("keyword SQL has content LIKE",
      "content LIKE '%marketing%'" in sql_kw)

# SQL injection prevention
inject_intent = ParsedIntent(
    filters={"from": "alice'; DROP TABLE memory; --"},
    query_type="structured",
)
sql_inject = ltm._build_intent_sql("user1", "gmail", inject_intent)
check("SQL injection escaped (single quote doubled)",
      "alice''; DROP TABLE memory; --" in sql_inject)
review_inject = reviewer.review(sql_inject, "user1")
check("injected SQL blocked by reviewer (semicolons)",
      not review_inject["approved"])

# build_from_intent static method
intent_dict = {
    "intent": "search", "limit": 15,
    "filters": {"unread": True}, "sort": "date_desc",
}
static_sql = SQLGenerator.build_from_intent("user1", "gmail", intent_dict)
check("static build_from_intent works",
      "LIMIT 15" in static_sql and "user_id = 'user1'" in static_sql)


# ============================================================
# Group 4: Deterministic Formatter (no API)
# ============================================================
print("\n=== Group 4: Deterministic Formatter ===")

agent = BaseAgent.__new__(BaseAgent)
agent.tool_name = "gmail"

# Gmail data
gmail_data = [
    {"subject": "Meeting Tomorrow", "from": "alice@corp.com",
     "date": "2026-02-20", "unread": True, "body": "Let's meet at 3pm."},
    {"subject": "Budget Review", "from": "bob@corp.com",
     "date": "2026-02-19", "unread": False, "body": "See attached."},
    {"subject": "Lunch Plans", "from": "carol@corp.com",
     "date": "2026-02-18", "unread": True, "body": "Pizza?"},
]

# Default fields (auto-detect)
intent_default = ParsedIntent(intent="search", limit=20, query_type="structured")
result = agent._format_exact_results(gmail_data, intent_default)
check("formatter returns formatted string", "formatted" in result)
check("formatter method is deterministic", result["method"] == "deterministic")
check("formatter shows 3 items", result["count"] == 3)
check("formatter contains Subject", "Subject: Meeting Tomorrow" in result["formatted"])
check("formatter contains From", "From: alice@corp.com" in result["formatted"])

# Specific fields
intent_fields = ParsedIntent(
    intent="search", limit=20, fields=["subject"],
    query_type="structured",
)
result_fields = agent._format_exact_results(gmail_data, intent_fields)
check("specific fields: shows subject",
      "Subject: Meeting Tomorrow" in result_fields["formatted"])
check("specific fields: no From shown",
      "From:" not in result_fields["formatted"])

# Count query
count_data = [{"count": 42}]
intent_count_fmt = ParsedIntent(intent="count", query_type="structured")
result_count = agent._format_exact_results(count_data, intent_count_fmt)
check("count formatter shows Count: 42",
      "Count: 42" in result_count["formatted"])

# Empty data
result_empty = agent._format_exact_results([], intent_default)
check("empty data returns 'No results found'",
      "No results found" in result_empty["formatted"])

# Long value truncation
long_data = [{"subject": "A" * 200, "from": "test@test.com"}]
result_long = agent._format_exact_results(long_data, intent_default)
check("long values truncated", "..." in result_long["formatted"])

# Slack data
agent_slack = BaseAgent.__new__(BaseAgent)
agent_slack.tool_name = "slack"
slack_data = [
    {"text": "Hello team!", "user": "alice", "channel": "general"},
]
result_slack = agent_slack._format_exact_results(slack_data, intent_default)
check("slack formatter shows Text/User/Channel",
      "Text: Hello team!" in result_slack["formatted"]
      and "User: alice" in result_slack["formatted"])


# ============================================================
# Group 5: Hybrid SQL Builder (no API)
# ============================================================
print("\n=== Group 5: Hybrid SQL Builder ===")

hybrid_intent = ParsedIntent(
    intent="search", limit=5,
    filters={"keyword": "marketing", "from": "alice"},
    query_type="hybrid",
)
candidate_ids = ["msg_001", "msg_002", "msg_003"]
sql_hybrid = ltm._build_hybrid_sql("user1", "gmail", hybrid_intent, candidate_ids)

check("hybrid SQL has external_id IN (...)",
      "external_id IN (" in sql_hybrid)
check("hybrid SQL includes candidate IDs",
      "'msg_001'" in sql_hybrid and "'msg_002'" in sql_hybrid)
check("hybrid SQL has from filter (keyword skipped)",
      "json_extract(content, '$.from') LIKE '%alice%'" in sql_hybrid)
check("hybrid SQL does NOT have keyword (already handled by ChromaDB)",
      "content LIKE '%marketing%'" not in sql_hybrid)
check("hybrid SQL has LIMIT 5", "LIMIT 5" in sql_hybrid)

review_hybrid = reviewer.review(sql_hybrid, "user1")
check("hybrid SQL passes safety review", review_hybrid["approved"])


# ============================================================
# Group 6: _build_final_answer with FORMATTED (no API)
# ============================================================
print("\n=== Group 6: Final Answer Builder ===")

# When FORMATTED is present, use it
results_with_fmt = {
    "SEARCH": {"data": gmail_data, "source": "structured_sql", "count": 3},
    "FORMATTED": {
        "formatted": "Found 3 result(s):\n1. Subject: Test",
        "method": "deterministic",
    },
}
answer = agent._build_final_answer("show emails", ["SEARCH"], results_with_fmt)
check("final answer uses FORMATTED when present",
      "Found 3 result(s)" in answer)
check("final answer does NOT show raw 'Found X results (from ...)'",
      "structured_sql" not in answer)

# When only SEARCH (no FORMATTED), shows source info
results_no_fmt = {
    "SEARCH": {"data": gmail_data, "source": "chromadb", "count": 3},
}
answer_no_fmt = agent._build_final_answer("find emails", ["SEARCH"], results_no_fmt)
check("without FORMATTED, shows source info",
      "chromadb" in answer_no_fmt)

# SUMMARIZE result (semantic path)
results_summarize = {
    "SEARCH": {"data": gmail_data, "source": "chromadb", "count": 3},
    "SUMMARIZE": {"summary": "Here are your emails about marketing."},
}
answer_summary = agent._build_final_answer(
    "emails about marketing", ["SEARCH", "SUMMARIZE"], results_summarize
)
check("semantic path shows summary text",
      "Here are your emails about marketing" in answer_summary)


# ============================================================
# Group 7: _resolve_intent backward compatibility
# ============================================================
print("\n=== Group 7: Backward Compatibility ===")

# None parsed_intent → default
intent_none = agent._resolve_intent(None)
check("None parsed_intent → default", intent_none.parsed_by == "default")
check("None → semantic query_type", intent_none.query_type == "semantic")
check("None → limit 20", intent_none.limit == 20)

# Valid dict → reconstructed
intent_dict = {
    "intent": "search", "limit": 10, "fields": ["subject"],
    "sort": "date_desc", "filters": {"from": "alice"},
    "query_type": "structured", "parsed_by": "llm",
}
intent_restored = agent._resolve_intent(intent_dict)
check("dict → restored intent", intent_restored.intent == "search")
check("dict → restored limit", intent_restored.limit == 10)
check("dict → restored fields", intent_restored.fields == ["subject"])
check("dict → restored query_type", intent_restored.query_type == "structured")

# Dict with unknown keys → still reconstructs (with defaults for missing fields)
intent_bad = agent._resolve_intent({"invalid": True})
check("unknown-key dict → reconstructed with defaults",
      intent_bad.intent == "search" and intent_bad.limit == 20)


# ============================================================
# Summary
# ============================================================
print(f"\n{'='*60}")
print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
if failed == 0:
    print("ALL TESTS PASSED")
else:
    print(f"FAILURES: {failed}")
print(f"{'='*60}")

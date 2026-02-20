"""
=============================================================================
SQL GENERATOR — LLM-Powered Smart SQL for Natural Language Queries
=============================================================================

WHY THIS EXISTS:
    The existing search in sqlite_manager.py uses dumb `content LIKE '%query%'`
    on the raw JSON blob. This causes:
      - False positives: "emails from John" matches John in subject, body, anywhere
      - Broken boolean search: "unread emails" finds nothing (JSON has "unread":true)
      - No date filtering: "emails from today" can't compare dates
      - No aggregation: "who sends me the most emails?" is impossible

    This module uses an LLM to generate precise SQLite queries with json_extract(),
    then validates them with a rule-based reviewer before execution.

ARCHITECTURE:
    ┌─────────────────────────────────────────────────────────────┐
    │  User query: "unread emails from john"                      │
    │                                                             │
    │  1. SQLGenerator._build_prompt()                            │
    │     → Builds LLM prompt with full schema context            │
    │                                                             │
    │  2. SQLGenerator._generate_sql()                            │
    │     → LLM (Groq) generates: SELECT * FROM memory           │
    │       WHERE user_id='u1' AND tool='gmail'                   │
    │       AND json_extract(content,'$.from') LIKE '%john%'      │
    │       AND json_extract(content,'$.unread') = 'true'         │
    │       ORDER BY cached_at DESC LIMIT 50                      │
    │                                                             │
    │  3. SQLReviewer.review()                                    │
    │     → 10-point safety check (rule-based, NOT LLM)           │
    │                                                             │
    │  4. db.execute_sql()                                        │
    │     → Executes the validated SQL                            │
    └─────────────────────────────────────────────────────────────┘

SAFETY:
    Two layers of defense:
      1. SQLReviewer (this file) — 10-point rule-based validation
      2. execute_sql() (sqlite_manager.py) — blocks mutations at DB layer

USAGE:
    from core.sql_generator import get_sql_generator

    sg = get_sql_generator()
    result = await sg.generate_and_execute("unread emails from john", "user_1", "gmail", db)
    # result = {"results": [...], "source": "llm_sql", "sql": "SELECT ..."}
    # or None if generation/review/execution failed

=============================================================================
"""

import re
import json
import logging
from datetime import datetime, timezone

from core.llm_manager import get_brain_router

logger = logging.getLogger(__name__)

# =============================================================================
# TOOL SCHEMA — Single source of truth for JSON field paths
# =============================================================================
# Both the LLM prompt and the SQLReviewer use this schema.
# If a new tool is added, add it here and everything else adapts.

TOOL_SCHEMA = {
    "gmail": {
        "fields": ["from", "to", "subject", "body", "date", "unread",
                    "labels", "has_attachment"],
    },
    "slack": {
        "fields": ["channel", "user", "text", "ts", "thread_ts",
                    "reactions"],
    },
    "outlook": {
        "fields": ["from", "subject", "body", "date", "start", "end",
                    "attendees", "is_read"],
    },
}

# Pre-compute the full set of allowed json_extract paths for fast validation
_ALL_ALLOWED_FIELDS = set()
for _tool, _schema in TOOL_SCHEMA.items():
    for _field in _schema["fields"]:
        _ALL_ALLOWED_FIELDS.add(f"$.{_field}")


# =============================================================================
# SQL REVIEWER — Rule-based safety validation (NOT LLM)
# =============================================================================

class SQLReviewer:
    """
    10-point safety validation for LLM-generated SQL.

    Defense-in-depth: catches problems BEFORE they reach execute_sql().
    Even if the reviewer misses something, execute_sql() blocks mutations.

    All checks are rule-based (regex/string matching), NOT LLM.
    This means 0ms overhead, deterministic, and no API costs.
    """

    # Dangerous SQL keywords that should never appear
    DANGEROUS_KEYWORDS = [
        "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "PRAGMA",
    ]

    # System tables that should never be queried
    SYSTEM_TABLES = [
        "sqlite_master", "sqlite_sequence", "sqlite_temp_master",
    ]

    # Only the memory table should be queried
    ALLOWED_TABLES = ["memory"]

    # Max result limit to prevent memory exhaustion
    MAX_LIMIT = 100

    # Max SQL length as sanity check
    MAX_LENGTH = 2000

    def review(self, sql, user_id):
        """
        Run all 10 safety checks on the SQL.

        Args:
            sql      (str): The SQL query to validate
            user_id  (str): Expected user_id that must appear in WHERE clause

        Returns:
            dict with:
                approved (bool): True if all checks pass
                reason   (str):  Why it was rejected (empty if approved)
        """
        if not sql or not isinstance(sql, str):
            return {"approved": False, "reason": "Empty or invalid SQL"}

        sql_stripped = sql.strip()
        sql_upper = sql_stripped.upper()

        # Check 1: Must start with SELECT
        if not sql_upper.startswith("SELECT"):
            return {"approved": False,
                    "reason": "Must start with SELECT"}

        # Check 2: No dangerous keywords
        for keyword in self.DANGEROUS_KEYWORDS:
            # Use word boundary to avoid matching "SELECTED" etc.
            if re.search(rf'\b{keyword}\b', sql_upper):
                return {"approved": False,
                        "reason": f"Dangerous keyword: {keyword}"}

        # Check 3: No semicolons (multi-statement injection)
        if ";" in sql_stripped:
            return {"approved": False,
                    "reason": "Semicolons not allowed (multi-statement)"}

        # Check 4: Only references memory table
        # Check that no other known tables are referenced
        tables_in_sql = re.findall(r'\bFROM\s+(\w+)', sql_upper)
        tables_in_sql += re.findall(r'\bJOIN\s+(\w+)', sql_upper)
        for table in tables_in_sql:
            if table.lower() not in self.ALLOWED_TABLES:
                return {"approved": False,
                        "reason": f"Forbidden table: {table}"}

        # Check 5: Contains user_id filter
        if user_id not in sql_stripped:
            return {"approved": False,
                    "reason": "Missing user_id filter"}

        # Check 6: LIMIT <= MAX_LIMIT
        limit_match = re.search(r'\bLIMIT\s+(\d+)', sql_upper)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > self.MAX_LIMIT:
                return {"approved": False,
                        "reason": f"LIMIT {limit_val} exceeds max {self.MAX_LIMIT}"}

        # Check 7: No SQL comments (-- or /* */)
        if "--" in sql_stripped or "/*" in sql_stripped:
            return {"approved": False,
                    "reason": "SQL comments not allowed"}

        # Check 8: Length < MAX_LENGTH
        if len(sql_stripped) > self.MAX_LENGTH:
            return {"approved": False,
                    "reason": f"SQL too long ({len(sql_stripped)} > {self.MAX_LENGTH})"}

        # Check 9: No system tables
        for sys_table in self.SYSTEM_TABLES:
            if sys_table.lower() in sql_stripped.lower():
                return {"approved": False,
                        "reason": f"System table reference: {sys_table}"}

        # Check 10: json_extract paths use known fields from TOOL_SCHEMA
        json_paths = re.findall(
            r"json_extract\s*\(\s*\w+\s*,\s*'(\$\.[^']+)'\s*\)",
            sql_stripped
        )
        for path in json_paths:
            if path not in _ALL_ALLOWED_FIELDS:
                return {"approved": False,
                        "reason": f"Unknown json_extract path: {path}"}

        return {"approved": True, "reason": ""}


# =============================================================================
# SQL GENERATOR — LLM-powered SQL generation
# =============================================================================

class SQLGenerator:
    """
    LLM-powered SQL generator. Analyzes user query, generates precise SQLite
    with json_extract(), validates via SQLReviewer, executes via db.execute_sql().

    Full pipeline: analyze query -> generate SQL -> review -> execute.
    Returns results or None on any failure (silent fallthrough).
    """

    # The LLM prompt template — tells the LLM the exact schema
    PROMPT_TEMPLATE = """You are a SQLite SQL generator for the Corporate Hassle Reducer app.

DATABASE SCHEMA:
  Table: memory
  Columns: id, user_id (TEXT), tool (TEXT), data_type (TEXT),
           external_id (TEXT), content (TEXT/JSON), cached_at (TEXT)

JSON FIELDS inside content column (access via json_extract):
  gmail:   $.from, $.to, $.subject, $.body, $.date, $.unread, $.labels, $.has_attachment
  slack:   $.channel, $.user, $.text, $.ts, $.thread_ts, $.reactions
  outlook: $.from, $.subject, $.body, $.date, $.start, $.end, $.attendees, $.is_read

RULES:
  1. Generate ONLY a SELECT statement — no INSERT, UPDATE, DELETE, DROP
  2. ALWAYS include WHERE user_id = '{user_id}'
  3. If the user mentions a tool, add WHERE tool = '<tool_name>'
  4. Use json_extract(content, '$.field') for JSON field access
  5. Use LIKE with % for text search (case-insensitive)
  6. For booleans: json_extract returns 1/0 in SQLite, so compare with 1 or 0 (e.g. json_extract(content,'$.unread') = 1)
  7. Always add ORDER BY cached_at DESC
  8. Always add LIMIT 50 unless the user asks for a specific count
  9. Return ONLY the raw SQL. No explanation, no markdown, no backticks.
  10. TODAY'S DATE is {today}. When the user says "today", "yesterday", "this week", etc., convert to actual date strings (YYYY-MM-DD) and use json_extract(content, '$.date') LIKE '%YYYY-MM-DD%'
  11. For Slack "from" queries, use json_extract(content, '$.user') — NOT $.from. Slack stores the sender in $.user
  12. ONLY use json_extract paths listed above. Do NOT invent new paths like $.attachments, $.sender, etc.
  13. For multi-tool queries (e.g. "check gmail and slack"), use tool IN ('gmail', 'slack') and query fields that exist for ALL mentioned tools

USER QUERY: {query}
TOOL CONTEXT: {tool}

SQL:"""

    def __init__(self):
        self.brain = get_brain_router()
        self.reviewer = SQLReviewer()

    async def generate_and_execute(self, query, user_id, tool=None, db=None):
        """
        Full pipeline: analyze query -> generate SQL -> review -> execute.

        Args:
            query   (str):  Natural language query from user
            user_id (str):  User ID for filtering
            tool    (str):  Tool context ("gmail", "slack", "outlook") or None
            db      (DatabaseManager): Database instance for execute_sql()

        Returns:
            dict with {"results": list, "source": "llm_sql", "sql": str}
            or None on any failure
        """
        if not query or not user_id or not db:
            return None

        # Step 1: Generate SQL via LLM
        sql = self._generate_sql(query, user_id, tool)
        if not sql:
            logger.debug("LLM SQL generation returned nothing")
            return None

        # Step 2: Review SQL for safety
        review = self.reviewer.review(sql, user_id)
        if not review["approved"]:
            logger.warning(f"SQL rejected by reviewer: {review['reason']} "
                           f"| SQL: {sql[:200]}")
            return None

        # Step 3: Execute the validated SQL
        try:
            rows = await db.execute_sql(sql)

            # Parse results — handle both normal rows and aggregation rows
            results = []
            for row in rows:
                if "content" in row:
                    # Normal row: parse the JSON content column
                    content = row.get("content", "{}")
                    if isinstance(content, str):
                        try:
                            content = json.loads(content)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    results.append(content)
                else:
                    # Aggregation row (GROUP BY, COUNT, etc.): return as-is
                    results.append(dict(row))

            logger.info(f"LLM SQL returned {len(results)} results | SQL: {sql[:100]}")
            return {
                "results": results,
                "source": "llm_sql",
                "sql": sql,
            }
        except Exception as e:
            logger.warning(f"LLM SQL execution failed: {e} | SQL: {sql[:200]}")
            return None

    def _generate_sql(self, query, user_id, tool=None):
        """
        Call Groq LLM with schema prompt. Returns raw SQL string or None.
        """
        prompt = self._build_prompt(query, user_id, tool)

        try:
            content = self.brain.invoke_with_fallback(prompt, task_type="fast")
            sql = content.strip()

            # Clean up: remove markdown backticks if LLM wraps the SQL
            if sql.startswith("```"):
                # Remove ```sql ... ``` wrapper
                lines = sql.split("\n")
                sql = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                ).strip()

            # Final cleanup: remove any trailing semicolons
            sql = sql.rstrip(";").strip()

            if not sql:
                return None

            return sql
        except Exception as e:
            logger.error(f"LLM SQL generation error: {e}")
            return None

    def _build_prompt(self, query, user_id, tool=None):
        """Build the LLM prompt with full schema context and today's date."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.PROMPT_TEMPLATE.format(
            user_id=user_id,
            query=query,
            tool=tool or "not specified",
            today=today,
        )


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_generator_instance = None


def get_sql_generator():
    """Get the singleton SQLGenerator instance."""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = SQLGenerator()
    return _generator_instance

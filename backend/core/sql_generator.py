"""SQL Generator — LLM-powered natural language to SQLite queries with safety review."""

import re
import json
import logging
from datetime import datetime, timezone

from core.llm_manager import get_brain_router

logger = logging.getLogger(__name__)

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

_ALL_ALLOWED_FIELDS = set()
for _tool, _schema in TOOL_SCHEMA.items():
    for _field in _schema["fields"]:
        _ALL_ALLOWED_FIELDS.add(f"$.{_field}")


class SQLReviewer:
    """10-point rule-based safety validation for LLM-generated SQL."""

    DANGEROUS_KEYWORDS = [
        "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "PRAGMA",
    ]
    SYSTEM_TABLES = ["sqlite_master", "sqlite_sequence", "sqlite_temp_master"]
    ALLOWED_TABLES = ["memory"]
    MAX_LIMIT = 100
    MAX_LENGTH = 2000

    def review(self, sql, user_id):
        """Run all safety checks. Returns {approved: bool, reason: str}."""
        if not sql or not isinstance(sql, str):
            return {"approved": False, "reason": "Empty or invalid SQL"}

        sql_stripped = sql.strip()
        sql_upper = sql_stripped.upper()

        if not sql_upper.startswith("SELECT"):
            return {"approved": False, "reason": "Must start with SELECT"}

        for keyword in self.DANGEROUS_KEYWORDS:
            if re.search(rf'\b{keyword}\b', sql_upper):
                return {"approved": False, "reason": f"Dangerous keyword: {keyword}"}

        if ";" in sql_stripped:
            return {"approved": False, "reason": "Semicolons not allowed (multi-statement)"}

        tables_in_sql = re.findall(r'\bFROM\s+(\w+)', sql_upper)
        tables_in_sql += re.findall(r'\bJOIN\s+(\w+)', sql_upper)
        for table in tables_in_sql:
            if table.lower() not in self.ALLOWED_TABLES:
                return {"approved": False, "reason": f"Forbidden table: {table}"}

        if user_id not in sql_stripped:
            return {"approved": False, "reason": "Missing user_id filter"}

        limit_match = re.search(r'\bLIMIT\s+(\d+)', sql_upper)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > self.MAX_LIMIT:
                return {"approved": False,
                        "reason": f"LIMIT {limit_val} exceeds max {self.MAX_LIMIT}"}

        if "--" in sql_stripped or "/*" in sql_stripped:
            return {"approved": False, "reason": "SQL comments not allowed"}

        if len(sql_stripped) > self.MAX_LENGTH:
            return {"approved": False,
                    "reason": f"SQL too long ({len(sql_stripped)} > {self.MAX_LENGTH})"}

        for sys_table in self.SYSTEM_TABLES:
            if sys_table.lower() in sql_stripped.lower():
                return {"approved": False, "reason": f"System table reference: {sys_table}"}

        json_paths = re.findall(
            r"json_extract\s*\(\s*\w+\s*,\s*'(\$\.[^']+)'\s*\)", sql_stripped
        )
        for path in json_paths:
            if path not in _ALL_ALLOWED_FIELDS:
                return {"approved": False, "reason": f"Unknown json_extract path: {path}"}

        return {"approved": True, "reason": ""}


class SQLGenerator:
    """LLM-powered SQL generator with safety review and execution."""

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
  8. {limit_instruction}
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

    async def generate_and_execute(self, query, user_id, tool=None, db=None,
                                   limit=None):
        """Full pipeline: generate SQL -> review -> execute. Returns dict or None."""
        if not query or not user_id or not db:
            return None

        sql = self._generate_sql(query, user_id, tool, limit=limit)
        if not sql:
            logger.debug("LLM SQL generation returned nothing")
            return None

        review = self.reviewer.review(sql, user_id)
        if not review["approved"]:
            logger.warning(f"SQL rejected by reviewer: {review['reason']} | SQL: {sql[:200]}")
            return None

        try:
            rows = await db.execute_sql(sql)

            results = []
            for row in rows:
                if "content" in row:
                    content = row.get("content", "{}")
                    if isinstance(content, str):
                        try:
                            content = json.loads(content)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    results.append(content)
                else:
                    # Aggregation row (GROUP BY, COUNT, etc.)
                    results.append(dict(row))

            logger.info(f"LLM SQL returned {len(results)} results | SQL: {sql[:100]}")
            return {"results": results, "source": "llm_sql", "sql": sql}
        except Exception as e:
            logger.warning(f"LLM SQL execution failed: {e} | SQL: {sql[:200]}")
            return None

    def _generate_sql(self, query, user_id, tool=None, limit=None):
        """Call LLM with schema prompt. Returns raw SQL string or None."""
        prompt = self._build_prompt(query, user_id, tool, limit=limit)

        try:
            content = self.brain.invoke_with_fallback(prompt, task_type="fast")
            sql = content.strip()

            if sql.startswith("```"):
                lines = sql.split("\n")
                sql = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                ).strip()

            sql = sql.rstrip(";").strip()
            return sql if sql else None
        except Exception as e:
            logger.error(f"LLM SQL generation error: {e}")
            return None

    def _build_prompt(self, query, user_id, tool=None, limit=None):
        """Build the LLM prompt with schema context and today's date."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        limit_instruction = (
            f"Use LIMIT {limit}" if limit
            else "Always add LIMIT 50 unless the user asks for a specific count"
        )
        return self.PROMPT_TEMPLATE.format(
            user_id=user_id, query=query,
            tool=tool or "not specified", today=today,
            limit_instruction=limit_instruction,
        )

    @staticmethod
    def build_from_intent(user_id, tool, intent_dict):
        """Build deterministic SQL from an intent dict — no LLM involved.

        This is the preferred path for structured queries where
        the QueryParser has already extracted validated parameters.
        """
        conditions = [f"user_id = '{user_id}'"]
        if tool:
            conditions.append(f"tool = '{tool}'")

        filters = intent_dict.get("filters", {})
        for key, value in filters.items():
            if value is None:
                continue
            escaped = str(value).replace("'", "''")

            if key == "keyword":
                conditions.append(f"content LIKE '%{escaped}%'")
            elif key == "date_from":
                conditions.append(
                    f"json_extract(content,'$.date') >= '{escaped}'")
            elif key == "date_to":
                conditions.append(
                    f"json_extract(content,'$.date') <= '{escaped}'")
            elif key in ("unread", "has_attachment", "is_read"):
                bool_val = 1 if value else 0
                conditions.append(
                    f"json_extract(content,'$.{key}') = {bool_val}")
            elif key in TOOL_SCHEMA.get(tool or "", {}).get("fields", []):
                conditions.append(
                    f"json_extract(content,'$.{key}') LIKE '%{escaped}%'")

        sort = intent_dict.get("sort", "date_desc")
        order = ("ORDER BY cached_at ASC" if sort == "date_asc"
                 else "ORDER BY cached_at DESC")
        limit = min(intent_dict.get("limit", 20), 100)

        where = " AND ".join(conditions)
        return f"SELECT content FROM memory WHERE {where} {order} LIMIT {limit}"


_generator_instance = None


def get_sql_generator():
    """Get the singleton SQLGenerator instance."""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = SQLGenerator()
    return _generator_instance

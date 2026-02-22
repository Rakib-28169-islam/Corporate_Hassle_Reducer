"""QueryParser — LLM-powered intent extraction with strict validation and
deterministic query_type override. LLM interprets, backend decides.

Phase 8: Unified LLM understanding via with_structured_output() + Pydantic schema.
"""

import re
import json
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from core.llm_manager import get_brain_router
from core.sql_generator import TOOL_SCHEMA

logger = logging.getLogger(__name__)


# --- Phase 8: Pydantic schema for structured LLM output ---

class Platform(str, Enum):
    """Target platform for the user's query."""
    GMAIL = "GMAIL"
    SLACK = "SLACK"
    OUTLOOK = "OUTLOOK"
    GENERAL = "GENERAL"


class OperationType(str, Enum):
    """Operations the system can perform."""
    SEARCH = "SEARCH"
    CALCULATE = "CALCULATE"
    ACTION = "ACTION"
    COMPOSE = "COMPOSE"
    SUMMARIZE = "SUMMARIZE"
    CHAT = "CHAT"


class QueryFilters(BaseModel):
    """Structured filters extracted from the user query.
    Only include fields the user explicitly mentioned."""
    model_config = ConfigDict(populate_by_name=True)

    from_sender: Optional[str] = Field(None, alias="from",
                                       description="Sender name or email")
    to: Optional[str] = Field(None, description="Recipient name or email")
    subject: Optional[str] = Field(None, description="Subject keyword")
    keyword: Optional[str] = Field(None,
                                   description="General topic/search term (about X, regarding X)")
    unread: Optional[bool] = Field(None, description="True for unread items")
    has_attachment: Optional[bool] = Field(None, description="True for items with attachments")
    is_read: Optional[bool] = Field(None, description="True for read items")
    date_phrase: Optional[str] = Field(None,
                                       description="Raw date text like 'last friday', 'march 5'")
    date_from: Optional[str] = Field(None, description="Start date YYYY-MM-DD")
    date_to: Optional[str] = Field(None, description="End date YYYY-MM-DD")
    channel: Optional[str] = Field(None, description="Slack channel name")
    user: Optional[str] = Field(None, description="Slack user name")
    labels: Optional[str] = Field(None, description="Gmail label")


class UnifiedUnderstanding(BaseModel):
    """Single LLM output schema — replaces classify + parse_intent + route.

    Returned by with_structured_output(), validated by Pydantic automatically.
    Backend still overrides query_type via IntentValidator._determine_query_type().
    """
    platform: Platform = Field(
        description="Target platform: GMAIL for email, SLACK for messages, "
                    "OUTLOOK for calendar/meetings, GENERAL for greetings/help")
    operations: list[OperationType] = Field(
        description="Operations needed: SEARCH, CALCULATE, ACTION, COMPOSE, "
                    "SUMMARIZE, CHAT")
    intent: str = Field(default="search",
                        description="search for fetching, count for counting, "
                                    "aggregate for grouping")
    limit: Optional[int] = Field(
        default=None,
        description="Number of results requested (e.g. 'last 10' = 10). "
                    "null if not specified.")
    fields: list[str] = Field(
        default_factory=list,
        description="Specific fields requested (e.g. 'titles' = ['subject']). "
                    "Empty = all fields.")
    sort: str = Field(default="date_desc",
                      description="date_desc (newest first) or date_asc (oldest first)")
    filters: QueryFilters = Field(
        default_factory=QueryFilters,
        description="Only include filters the user explicitly mentioned")


# System prompt for unified understanding (no JSON formatting instructions needed —
# schema enforcement is handled by with_structured_output())
UNDERSTAND_PROMPT = """You are a query understanding engine for a corporate assistant \
that manages Gmail, Slack, and Outlook.

Given a user query, extract the platform, operations, intent, and parameters.

PLATFORM RULES:
- "email", "inbox", "mail", "unread" without other context → GMAIL
- "meeting", "calendar", "schedule", "event", "appointment" → OUTLOOK
- "channel", "DM", "slack", "message" (in Slack context) → SLACK
- Greetings, help, general questions → GENERAL

OPERATION RULES:
- SEARCH: user wants to find/show/list/view/read/describe data
- CALCULATE: user wants to count, compare, or compute (also needs SEARCH)
- ACTION: user wants to send, delete, move, reply, forward, book, schedule
- COMPOSE: user wants to draft, write, prepare content
- SUMMARIZE: user wants a summary, overview, recap (also needs SEARCH)
- CHAT: greetings, general questions, help

FIELD NAME RULES (use these exact canonical names):
- Gmail fields: from, to, subject, body, date, unread, labels, has_attachment
- Slack fields: channel, user, text, ts
- Outlook fields: from, subject, body, date, start, end, attendees, is_read
- "titles" / "title" = subject
- "sender" / "senders" = from
- "content" / "message" / "text" (email) = body

FILTER RULES:
- ONLY include filters the user explicitly mentioned. Never guess.
- "from" = sender filter. For Slack, use "user" instead of "from"
- "keyword" = topic/concept when user says "about X", "related to X", "regarding X"
- "date_phrase" = put the RAW date text ("yesterday", "march 5", "last week"). Backend converts.
- For counting ("how many"), set intent="count" and include relevant filters

TODAY'S DATE: {today}

User Query: {query}"""


# Common LLM field aliases → canonical field names
_FIELD_ALIASES = {
    "titles": "subject", "title": "subject",
    "sender": "from", "senders": "from",
    "content": "body", "message": "body",
    "email_body": "body", "message_body": "body",
    "recipient": "to", "recipients": "to",
    "timestamp": "date", "time": "date", "sent_at": "date",
    "attachment": "has_attachment", "attachments": "has_attachment",
    "read": "is_read",
}


def post_validate(understanding, query=None):
    """Convert UnifiedUnderstanding to a validated ParsedIntent.

    Backend ALWAYS overrides query_type — IntentValidator._determine_query_type()
    is the source of truth, not the LLM.
    """
    # Convert Pydantic model to raw dict for existing validator
    filters_dict = understanding.filters.model_dump(
        by_alias=True, exclude_none=True,
    )

    # Map common LLM field aliases to canonical names
    canonical_fields = []
    for f in understanding.fields:
        canonical = _FIELD_ALIASES.get(f.lower(), f.lower())
        if canonical not in canonical_fields:
            canonical_fields.append(canonical)

    raw = {
        "intent": understanding.intent,
        "limit": understanding.limit,
        "fields": canonical_fields,
        "sort": understanding.sort,
        "filters": filters_dict,
    }

    # Detect tool context for field whitelist validation
    tool = (understanding.platform.value.lower()
            if understanding.platform != Platform.GENERAL else None)

    validator = IntentValidator()
    intent = validator.validate(raw, tool=tool)

    logger.info(
        f"[post_validate] platform={understanding.platform.value} | "
        f"ops={[op.value for op in understanding.operations]} | "
        f"intent={intent.intent}/{intent.query_type} | "
        f"limit={intent.limit} | filters={intent.filters}"
    )
    return intent

# --- Schema constants ---

VALID_INTENTS = {"search", "count", "aggregate", "list"}
VALID_QUERY_TYPES = {"structured", "semantic", "hybrid"}
VALID_SORT_OPTIONS = {"date_desc", "date_asc"}

VALID_FIELDS = {
    tool: set(schema["fields"]) for tool, schema in TOOL_SCHEMA.items()
}
ALL_VALID_FIELDS = set()
for _fields in VALID_FIELDS.values():
    ALL_VALID_FIELDS.update(_fields)

EXACT_FILTER_KEYS = {"from", "to", "unread", "has_attachment", "is_read",
                      "labels", "channel", "user", "subject"}
DATE_FILTER_KEYS = {"date_from", "date_to", "date_phrase"}
SEMANTIC_FILTER_KEYS = {"keyword"}
VALID_FILTER_KEYS = EXACT_FILTER_KEYS | DATE_FILTER_KEYS | SEMANTIC_FILTER_KEYS


class ParsedIntent:
    """Validated, immutable intent object."""

    __slots__ = ("intent", "limit", "fields", "sort", "filters",
                 "query_type", "raw_json", "parsed_by")

    def __init__(self, intent="search", limit=20, fields=None, sort="date_desc",
                 filters=None, query_type="semantic", raw_json=None,
                 parsed_by="default"):
        self.intent = intent
        self.limit = limit
        self.fields = fields or []
        self.sort = sort
        self.filters = filters or {}
        self.query_type = query_type
        self.raw_json = raw_json
        self.parsed_by = parsed_by

    def to_dict(self):
        return {
            "intent": self.intent,
            "limit": self.limit,
            "fields": list(self.fields),
            "sort": self.sort,
            "filters": dict(self.filters),
            "query_type": self.query_type,
            "parsed_by": self.parsed_by,
        }

    @staticmethod
    def default():
        return ParsedIntent(parsed_by="default")


# --- Deterministic date parser ---

def parse_date_phrase(phrase, today=None):
    """Convert relative/natural date phrases to YYYY-MM-DD deterministically.
    Returns date string or None if unparseable.
    """
    if not phrase or not isinstance(phrase, str):
        return None

    if today is None:
        today = datetime.now(timezone.utc).date()

    phrase_lower = phrase.strip().lower()

    # Already a valid date string
    if re.match(r'^\d{4}-\d{2}-\d{2}$', phrase_lower):
        return phrase_lower

    # Relative dates
    RELATIVE_MAP = {
        "today": today,
        "yesterday": today - timedelta(days=1),
        "day before yesterday": today - timedelta(days=2),
    }
    if phrase_lower in RELATIVE_MAP:
        return RELATIVE_MAP[phrase_lower].strftime("%Y-%m-%d")

    # "last X days"
    m = re.match(r'last\s+(\d+)\s+days?', phrase_lower)
    if m:
        days = int(m.group(1))
        return (today - timedelta(days=days)).strftime("%Y-%m-%d")

    # Weekday names ("last monday", "last friday")
    WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6}
    m = re.match(r'(?:last\s+)?(\w+day)', phrase_lower)
    if m and m.group(1) in WEEKDAYS:
        target_wd = WEEKDAYS[m.group(1)]
        current_wd = today.weekday()
        days_back = (current_wd - target_wd) % 7
        if days_back == 0:
            days_back = 7
        return (today - timedelta(days=days_back)).strftime("%Y-%m-%d")

    # "this week" → Monday of current week
    if phrase_lower in ("this week", "current week"):
        monday = today - timedelta(days=today.weekday())
        return monday.strftime("%Y-%m-%d")

    # "last week" → Monday of previous week
    if phrase_lower == "last week":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday.strftime("%Y-%m-%d")

    # Month + day: "March 5", "march 5th", "5 march", "feb 14"
    MONTHS = {
        "jan": 1, "january": 1, "feb": 2, "february": 2,
        "mar": 3, "march": 3, "apr": 4, "april": 4,
        "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }

    # "March 5" or "March 5th"
    m = re.match(r'(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?$', phrase_lower)
    if m and m.group(1) in MONTHS:
        month = MONTHS[m.group(1)]
        day = int(m.group(2))
        year = today.year
        # If date is in the future, assume last year
        try:
            d = datetime(year, month, day).date()
            if d > today:
                d = datetime(year - 1, month, day).date()
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # "5 March" or "5th March"
    m = re.match(r'(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)$', phrase_lower)
    if m and m.group(2) in MONTHS:
        month = MONTHS[m.group(2)]
        day = int(m.group(1))
        year = today.year
        try:
            d = datetime(year, month, day).date()
            if d > today:
                d = datetime(year - 1, month, day).date()
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


# --- Intent Validator ---

class IntentValidator:
    """Rule-based validation + deterministic query_type override."""

    def validate(self, raw, tool=None):
        """Validate raw LLM JSON into a ParsedIntent. Never raises."""
        if not isinstance(raw, dict):
            return ParsedIntent.default()

        intent = self._validate_intent(raw.get("intent"))
        limit = self._validate_limit(raw.get("limit"))
        fields = self._validate_fields(raw.get("fields"), tool)
        sort = self._validate_sort(raw.get("sort"))
        filters = self._validate_filters(raw.get("filters"))

        # Resolve date phrases in filters
        filters = self._resolve_dates(filters)

        # Backend OVERRIDES LLM's query_type — never trust LLM classification
        query_type = self._determine_query_type(intent, filters)

        return ParsedIntent(
            intent=intent, limit=limit, fields=fields, sort=sort,
            filters=filters, query_type=query_type, raw_json=raw,
            parsed_by="llm",
        )

    def _validate_intent(self, value):
        if isinstance(value, str) and value.lower() in VALID_INTENTS:
            return value.lower()
        return "search"

    def _validate_limit(self, value):
        if value is None:
            return 20
        try:
            limit = int(value)
            return max(1, min(100, limit))
        except (TypeError, ValueError):
            return 20

    def _validate_fields(self, value, tool=None):
        if not isinstance(value, list):
            return []
        allowed = VALID_FIELDS.get(tool, ALL_VALID_FIELDS)
        return [f for f in value if isinstance(f, str) and f in allowed]

    def _validate_sort(self, value):
        if isinstance(value, str) and value in VALID_SORT_OPTIONS:
            return value
        return "date_desc"

    def _validate_filters(self, value):
        if not isinstance(value, dict):
            return {}
        clean = {}
        for key, val in value.items():
            if key not in VALID_FILTER_KEYS:
                continue
            if val is None:
                continue
            if isinstance(val, (str, int, float, bool)):
                clean[key] = val
        return clean

    def _resolve_dates(self, filters):
        """Convert date_phrase to date_from/date_to using deterministic parser."""
        phrase = filters.pop("date_phrase", None)
        if phrase:
            resolved = parse_date_phrase(str(phrase))
            if resolved:
                if "date_from" not in filters:
                    filters["date_from"] = resolved
                if "date_to" not in filters:
                    filters["date_to"] = resolved

        # Also validate existing date_from/date_to (LLM might give wrong format)
        for key in ("date_from", "date_to"):
            if key in filters:
                val = str(filters[key])
                parsed = parse_date_phrase(val)
                if parsed:
                    filters[key] = parsed
                elif not re.match(r'^\d{4}-\d{2}-\d{2}$', val):
                    del filters[key]

        return filters

    def _determine_query_type(self, intent, filters):
        """Deterministic query_type override — NEVER trust LLM for this.

        Rules:
        - count/aggregate → always structured
        - has exact filters (from, unread, date, etc.) + keyword → hybrid
        - has exact filters only → structured
        - has keyword only → semantic
        - no filters → structured (default to recent items)
        """
        if intent in ("count", "aggregate"):
            return "structured"

        has_exact = bool(EXACT_FILTER_KEYS & set(filters.keys()))
        has_date = bool(DATE_FILTER_KEYS & set(filters.keys()))
        has_keyword = "keyword" in filters

        if (has_exact or has_date) and has_keyword:
            return "hybrid"
        if has_exact or has_date:
            return "structured"
        if has_keyword:
            return "semantic"

        # No filters at all → structured (fetch recent items)
        return "structured"


# --- Query Parser ---

class QueryParser:
    """LLM intent extraction with strict JSON enforcement + validation."""

    PARSE_PROMPT = """You are a query intent parser for a corporate assistant.
Extract structured parameters from the user's query.

CRITICAL: Output ONLY valid JSON. No explanation. No markdown. No extra text.

TOOL CONTEXT: {tool}
TODAY'S DATE: {today}

OUTPUT FORMAT:
{{
  "intent": "search" | "count" | "aggregate",
  "limit": <integer 1-100 or null for default>,
  "fields": ["subject", "from", "date", ...] or [],
  "sort": "date_desc" | "date_asc",
  "filters": {{
    "from": "<sender>",
    "to": "<recipient>",
    "subject": "<subject keyword>",
    "keyword": "<general topic/search term>",
    "unread": true | false,
    "has_attachment": true | false,
    "date_from": "YYYY-MM-DD",
    "date_to": "YYYY-MM-DD",
    "date_phrase": "<raw date text like 'last friday', 'march 5'>",
    "channel": "<slack channel>",
    "user": "<slack user>",
    "labels": "<gmail label>"
  }},
  "query_type": "structured" | "semantic" | "hybrid"
}}

RULES:
1. "intent" = "search" for fetching, "count" for counting, "aggregate" for grouping
2. "limit" = extract from "last 10", "top 5", "recent 3". null if not mentioned.
3. "fields" = only include if user explicitly asks for specific fields. "titles" = ["subject"]. "senders" = ["from"]. Empty = all fields.
4. "filters" = ONLY include fields the user explicitly mentioned. Never guess.
5. "date_phrase" = if user says relative date ("March 5", "yesterday", "last week"), put the RAW phrase here. Backend converts it.
6. "keyword" = topic/concept when user says "about X", "related to X", "regarding X"
7. "query_type" = "structured" for exact queries, "semantic" for topic/meaning queries, "hybrid" for both
8. For counts: intent="count", NOT intent="search" with "how many" in keyword
9. For Slack "from" queries, use "user" filter NOT "from"
10. Only output valid JSON. Nothing else.

EXAMPLES:
"show last 10 email titles" -> {{"intent":"search","limit":10,"fields":["subject"],"sort":"date_desc","filters":{{}},"query_type":"structured"}}
"unread emails from alice" -> {{"intent":"search","limit":null,"fields":[],"sort":"date_desc","filters":{{"from":"alice","unread":true}},"query_type":"structured"}}
"how many unread emails" -> {{"intent":"count","limit":100,"fields":[],"sort":"date_desc","filters":{{"unread":true}},"query_type":"structured"}}
"emails about marketing strategy" -> {{"intent":"search","limit":null,"fields":[],"sort":"date_desc","filters":{{"keyword":"marketing strategy"}},"query_type":"semantic"}}
"last 5 emails related to budget" -> {{"intent":"search","limit":5,"fields":[],"sort":"date_desc","filters":{{"keyword":"budget"}},"query_type":"hybrid"}}
"emails from March 5" -> {{"intent":"search","limit":null,"fields":[],"sort":"date_desc","filters":{{"date_phrase":"March 5"}},"query_type":"structured"}}
"show my calendar events for tomorrow" -> {{"intent":"search","limit":null,"fields":[],"sort":"date_asc","filters":{{"date_phrase":"tomorrow"}},"query_type":"structured"}}

USER QUERY: {query}

JSON:"""

    def __init__(self):
        self.brain = get_brain_router()
        self.validator = IntentValidator()
        logger.info("QueryParser initialized")

    def parse(self, query, tool=None):
        """Extract structured intent. Returns ParsedIntent (always valid)."""
        try:
            raw_json = self._call_llm(query, tool)
            if raw_json is None:
                # Retry once on JSON parse failure
                logger.warning("QueryParser: first LLM attempt failed, retrying")
                raw_json = self._call_llm(query, tool)

            if raw_json is None:
                logger.warning("QueryParser: LLM returned no valid JSON, using defaults")
                return ParsedIntent.default()

            intent = self.validator.validate(raw_json, tool)

            # Audit log
            logger.info(
                f"[QueryParser] query='{query[:60]}' | "
                f"intent={intent.intent} | type={intent.query_type} | "
                f"limit={intent.limit} | filters={intent.filters}"
            )
            return intent

        except Exception as e:
            logger.error(f"QueryParser.parse failed: {e}")
            return ParsedIntent.default()

    def _call_llm(self, query, tool=None):
        """Call LLM with strict JSON prompt. Returns dict or None."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        prompt = self.PARSE_PROMPT.format(
            query=query,
            tool=tool or "not specified",
            today=today,
        )

        try:
            content = self.brain.invoke_with_fallback(prompt, task_type="fast")
        except Exception as e:
            logger.error(f"QueryParser LLM call failed: {e}")
            return None

        return self._extract_json(content)

    def _extract_json(self, text):
        """Extract JSON from LLM response. Two-pass: direct parse then brace finding."""
        if not text:
            return None

        text = text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()

        # Pass 1: direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Pass 2: find JSON object in response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        logger.warning(f"QueryParser: could not parse JSON: {text[:200]}")
        return None


# --- Singleton ---

_parser_instance = None


def get_query_parser():
    """Get the singleton QueryParser instance."""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = QueryParser()
    return _parser_instance

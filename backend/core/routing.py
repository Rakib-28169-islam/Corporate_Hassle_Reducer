"""
QueryRouter — Extracted routing logic from SupervisorAgent.

Two-layer routing:
  Layer 1: Keyword match via n-gram hashmap (instant, free)
  Layer 2: LLM classification via BrainRouter (only if keywords fail)

This is a verbatim extraction — zero behavior change from the original
supervisor routing code. The supervisor now delegates to this class.
"""

import logging

from core.llm_manager import get_brain_router

logger = logging.getLogger(__name__)


class QueryRouter:
    """
    Routes user queries to the correct platform agent.

    Uses the same two-layer approach as the original SupervisorAgent:
      1. Fast keyword hashmap (O(W) where W = words in query)
      2. LLM fallback for ambiguous queries
    """

    # --- Keyword Pre-Router (instant, no API call) ---
    KEYWORD_RULES = {
        "GMAIL": [
            "gmail", "g-mail", "google mail",
            "inbox", "unread", "read email", "open email", "check email",
            "new email", "latest email", "recent email", "my email",
            "send email", "compose email", "write email", "draft email",
            "email draft", "compose", "new draft",
            "reply email", "forward email", "reply to",
            "label", "star", "starred", "important", "archive",
            "spam", "junk", "trash", "delete email",
            "search email", "find email", "email from", "email about",
            "attachment", "attached file", "download attachment",
            "newsletter", "unsubscribe", "promotions", "cc", "bcc",
            "subject line", "mail", "mailing",
        ],
        "SLACK": [
            "slack",
            "channel", "dm", "direct message", "post message",
            "send message", "slack message", "write message",
            "find channel", "find user", "workspace", "member",
            "reaction", "emoji", "react to", "thumbs up",
            "conversation history", "channel history", "recent messages",
            "pin", "pinned", "bookmark", "huddle", "status",
            "mention", "notify channel", "notification",
            "remind", "reminder slack",
        ],
        "OUTLOOK": [
            "outlook", "microsoft mail", "office mail", "office 365",
            "calendar", "meeting", "meetings", "schedule", "event", "appointment",
            "booking", "invite", "attendee", "agenda",
            "upcoming event", "next meeting", "today meeting",
            "create event", "book meeting", "schedule meeting",
            "reschedule", "cancel meeting", "recurring",
            "conference room", "room booking",
            "contacts", "address book", "contact list",
            "outlook email", "outlook inbox", "outlook draft",
            "work email", "office email",
            "teams", "teams meeting", "teams call",
        ],
    }

    # Platform identifiers that instantly lock the route (highest priority)
    PLATFORM_NAMES = {
        "GMAIL": ["gmail", "g-mail", "google mail", "GMAIL", "Gmail",
                   "G-mail", "Google Mail"],
        "SLACK": ["slack"],
        "OUTLOOK": ["outlook", "work email", "office email", "office 365",
                     "microsoft mail"],
    }

    # --- LLM Router (only for ambiguous queries) ---
    ROUTING_PROMPT = """You are a query router for a corporate assistant.
Classify the user's query into ONE of these categories:

- GMAIL: Anything about Gmail, personal email, drafts, labels, inbox, threads
- SLACK: Anything about Slack, channels, messages, notifications, reactions, workspace
- OUTLOOK: Anything about Outlook, work email, calendar events, meetings, scheduling, contacts
- GENERAL: Greetings, general questions, unclear requests, or multi-platform queries

Rules:
- If the user says "email" without specifying, default to GMAIL
- If the user mentions "meeting" or "calendar", route to OUTLOOK
- If the user mentions "channel" or "DM", route to SLACK
- Return ONLY the category name (GMAIL, SLACK, OUTLOOK, or GENERAL). Nothing else.

User Query: {query}

Category:"""

    def __init__(self):
        self.brain = get_brain_router()
        self._platform_index = self._build_index(self.PLATFORM_NAMES)
        self._keyword_index = self._build_index(self.KEYWORD_RULES)
        logger.info("QueryRouter initialized with pre-built keyword indexes.")

    @staticmethod
    def _build_index(rules):
        """
        Converts keyword lists into hashmaps for O(1) lookup.
        Single words  -> {word: agent}          (1-gram)
        Two words     -> {"word1 word2": agent}  (2-gram)
        Three words   -> {"w1 w2 w3": agent}     (3-gram)
        """
        index = {"1g": {}, "2g": {}, "3g": {}}
        for agent, keywords in rules.items():
            for kw in keywords:
                parts = kw.split()
                if len(parts) == 1:
                    index["1g"][kw] = agent
                elif len(parts) == 2:
                    index["2g"][kw] = agent
                else:
                    index["3g"][kw] = agent
        return index

    def _search_index(self, index, words, bigrams, trigrams):
        """Search a hashmap index using pre-computed n-grams."""
        for tg in trigrams:
            if tg in index["3g"]:
                return index["3g"][tg]
        for bg in bigrams:
            if bg in index["2g"]:
                return index["2g"][bg]
        for w in words:
            if w in index["1g"]:
                return index["1g"][w]
        return None

    @staticmethod
    def _clean_query(query):
        """Strip punctuation, lowercase, split into words."""
        clean = query.lower()
        for ch in "?!.,;:'\"()[]{}":
            clean = clean.replace(ch, "")
        return clean.split()

    def _keyword_route(self, query):
        """
        FAST PATH: Hashmap-based keyword matching.
        O(W) where W = number of words in query.
        """
        words = self._clean_query(query)
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
        trigrams = [f"{words[i]} {words[i+1]} {words[i+2]}"
                    for i in range(len(words)-2)]

        # Priority 1: Explicit platform name
        result = self._search_index(self._platform_index, words, bigrams,
                                    trigrams)
        if result:
            return result

        # Priority 2: General keywords
        result = self._search_index(self._keyword_index, words, bigrams,
                                    trigrams)
        return result

    def _llm_route(self, query):
        """SLOW PATH: Use AI to classify ambiguous queries (~300-500ms)."""
        try:
            content = self.brain.invoke_with_fallback(
                self.ROUTING_PROMPT.format(query=query),
                task_type="router",
            )
            route = content.strip().upper()

            for key in self.KEYWORD_RULES:
                if key in route:
                    return key

            return "GENERAL"
        except Exception as e:
            logger.error(f"Routing failed (all LLMs): {e}")
            return "GENERAL"

    def route_query(self, query):
        """
        Smart 2-layer routing:
          Layer 1: Keyword match (instant, free)
          Layer 2: LLM classification (only if keywords fail)

        Returns:
            tuple: (route, routed_by) e.g. ("GMAIL", "keyword")
        """
        route = self._keyword_route(query)
        if route:
            return route, "keyword"

        route = self._llm_route(query)
        return route, "llm"


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_router_instance = None


def get_router():
    """Get the singleton QueryRouter instance."""
    global _router_instance
    if _router_instance is None:
        _router_instance = QueryRouter()
    return _router_instance


# =============================================================================
# TEST BLOCK
# =============================================================================
if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    print("Testing QueryRouter...")
    print("=" * 60)
    router = QueryRouter()

    test_queries = [
        "Check my unread emails",
        "Send a Slack message to #general",
        "What meetings do I have today?",
        "Hello, how are you?",
        "Draft an email to boss@company.com",
        "Search Slack for project updates",
        "Create a calendar event for tomorrow",
        "Check is there any emails from Alice in gmail?",
    ]

    print("\nRouting Tests:")
    print("-" * 60)
    for q in test_queries:
        route, method = router.route_query(q)
        print(f"  [{route:8s}] ({method:7s}) {q}")

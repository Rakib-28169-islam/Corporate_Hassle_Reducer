"""QueryRouter — two-layer routing: keyword hashmap (instant) + LLM fallback."""

import logging

from core.llm_manager import get_brain_router

logger = logging.getLogger(__name__)


class QueryRouter:
    """Routes user queries to the correct platform agent."""

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
            "subject line", "mail", "mailing", "emails", "email",
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
        "GENERAL": [
            "hi", "hello", "hey", "howdy", "sup", "yo",
            "good morning", "good afternoon", "good evening",
            "thanks", "thank you", "thx",
            "bye", "goodbye", "see you",
            "who are you", "what are you", "what can you do",
            "whats your agenda", "your agenda",
            "how does this work", "help me",
            "ok", "okay", "sure",
        ],
    }

    PLATFORM_NAMES = {
        "GMAIL": ["gmail", "g-mail", "google mail", "GMAIL", "Gmail",
                   "G-mail", "Google Mail"],
        "SLACK": ["slack"],
        "OUTLOOK": ["outlook", "work email", "office email", "office 365",
                     "microsoft mail"],
    }

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
        platform_rules = {k: v for k, v in self.KEYWORD_RULES.items() if k != "GENERAL"}
        general_rules = {k: v for k, v in self.KEYWORD_RULES.items() if k == "GENERAL"}
        self._keyword_index = self._build_index(platform_rules)
        self._general_index = self._build_index(general_rules)
        logger.info("QueryRouter initialized with pre-built keyword indexes.")

    @staticmethod
    def _build_index(rules):
        """Converts keyword lists into n-gram hashmaps for O(1) lookup."""
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
        """Fast keyword-based routing.

        Priority (longest match = most specific intent):
          1. Platform names (explicit: "gmail", "slack", "outlook")
          2. Multi-word matches (3g, 2g) from platform AND general
          3. Platform single-word keywords (1g)
          4. General single-word keywords (1g)

        This ensures "whats your agenda" (GENERAL 3g) beats "agenda" (OUTLOOK 1g).
        """
        words = self._clean_query(query)
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
        trigrams = [f"{words[i]} {words[i+1]} {words[i+2]}"
                    for i in range(len(words)-2)]

        # Priority 1: Platform names (explicit mentions)
        result = self._search_index(self._platform_index, words, bigrams, trigrams)
        if result:
            return result

        # Priority 2: Multi-word phrases (3g then 2g) — check BOTH indexes
        for tg in trigrams:
            if tg in self._keyword_index["3g"]:
                return self._keyword_index["3g"][tg]
            if tg in self._general_index["3g"]:
                return self._general_index["3g"][tg]

        for bg in bigrams:
            if bg in self._keyword_index["2g"]:
                return self._keyword_index["2g"][bg]
            if bg in self._general_index["2g"]:
                return self._general_index["2g"][bg]

        # Priority 3: Platform single keywords (1g)
        for w in words:
            if w in self._keyword_index["1g"]:
                return self._keyword_index["1g"][w]

        # Priority 4: General single keywords (1g)
        for w in words:
            if w in self._general_index["1g"]:
                return self._general_index["1g"][w]

        return None

    def _llm_route(self, query):
        """LLM fallback for ambiguous queries."""
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
        """Two-layer routing: keyword match first, then LLM fallback. Returns (route, routed_by)."""
        route = self._keyword_route(query)
        if route:
            return route, "keyword"

        route = self._llm_route(query)
        return route, "llm"


_router_instance = None


def get_router():
    """Get the singleton QueryRouter instance."""
    global _router_instance
    if _router_instance is None:
        _router_instance = QueryRouter()
    return _router_instance

import sys
import os
import json
import logging

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_manager import get_brain_router
from agents.gmail_agent import GmailAgent
from agents.slack_agent import SlackAgent
from agents.outlook_agent import OutlookAgent

logger = logging.getLogger(__name__)


class SupervisorAgent:
    """
    The Mother Agent (Supervisor).
    Routes user queries to the correct specialist agent.
    Brain: Groq (fast routing) for classification, then delegates to specialists.

    Flow:
      User Query -> Supervisor classifies -> Routes to correct agent -> Returns result
    """

    # --- Keyword Pre-Router (instant, no API call) ---
    KEYWORD_RULES = {
        "GMAIL": [
            # Platform
            "gmail", "g-mail", "google mail",
            # Inbox & reading
            "inbox", "unread", "read email", "open email", "check email",
            "new email", "latest email", "recent email", "my email",
            # Compose & send
            "send email", "compose email", "write email", "draft email",
            "email draft", "compose", "new draft",
            # Reply & forward
            "reply email", "forward email", "reply to",
            # Organize
            "label", "star", "starred", "important", "archive",
            "spam", "junk", "trash", "delete email",
            # Search
            "search email", "find email", "email from", "email about",
            # Attachments
            "attachment", "attached file", "download attachment",
            # Misc
            "newsletter", "unsubscribe", "promotions", "cc", "bcc",
            "subject line", "mail", "mailing",
        ],
        "SLACK": [
            # Platform
            "slack",
            # Messaging
            "channel", "dm", "direct message", "post message",
            "send message", "slack message", "write message",
            # Discovery
            "find channel", "find user", "workspace", "member",
            # Reactions & engagement
            "reaction", "emoji", "react to", "thumbs up",
            # History
            "conversation history", "channel history", "recent messages",
            # Features
            "pin", "pinned", "bookmark", "huddle", "status",
            "mention", "notify channel", "notification",
            "remind", "reminder slack",
        ],
        "OUTLOOK": [
            # Platform
            "outlook", "microsoft mail", "office mail", "office 365",
            # Calendar
            "calendar", "meeting", "schedule", "event", "appointment",
            "booking", "invite", "attendee", "agenda",
            "upcoming event", "next meeting", "today meeting",
            # Calendar actions
            "create event", "book meeting", "schedule meeting",
            "reschedule", "cancel meeting", "recurring",
            "conference room", "room booking",
            # Contacts
            "contacts", "address book", "contact list",
            # Outlook email
            "outlook email", "outlook inbox", "outlook draft",
            "work email", "office email",
            # Teams integration
            "teams", "teams meeting", "teams call",
        ],
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

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.brain = get_brain_router()

        # Build hashmap indexes once at init (O(1) lookup later)
        self._platform_index = self._build_index(self.PLATFORM_NAMES)
        self._keyword_index = self._build_index(self.KEYWORD_RULES)

        # Specialist agents (created on demand)
        self._gmail = None
        self._slack = None
        self._outlook = None

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

    # --- Lazy loading: agents only created when needed ---
    @property
    def gmail(self):
        if self._gmail is None:
            self._gmail = GmailAgent(user_id=self.user_id)
        return self._gmail

    @property
    def slack(self):
        if self._slack is None:
            self._slack = SlackAgent(user_id=self.user_id)
        return self._slack

    @property
    def outlook(self):
        if self._outlook is None:
            self._outlook = OutlookAgent(user_id=self.user_id)
        return self._outlook

    # ==================== ROUTING ====================

    # Platform identifiers that instantly lock the route (highest priority)
    PLATFORM_NAMES = {
        "GMAIL": ["gmail", "g-mail", "google mail"],
        "SLACK": ["slack"],
        "OUTLOOK": ["outlook", "work email", "office email", "office 365", "microsoft mail"],
    }

    def _search_index(self, index, words, bigrams, trigrams):
        """
        Search a hashmap index using pre-computed n-grams.
        All lookups are O(1) dict access. Total: O(W) where W = words in query.
        """
        # Check trigrams (3-word phrases)
        for tg in trigrams:
            if tg in index["3g"]:
                return index["3g"][tg]
        # Check bigrams (2-word phrases)
        for bg in bigrams:
            if bg in index["2g"]:
                return index["2g"][bg]
        # Check single words
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
        O(W) where W = number of words in query. Each lookup is O(1).

        Old approach: O(K * Q) - scan all K keywords against Q-length string
        New approach: O(W) - split query into W words, check hashmap
        """
        words = self._clean_query(query)
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
        trigrams = [f"{words[i]} {words[i+1]} {words[i+2]}" for i in range(len(words)-2)]

        # Priority 1: Explicit platform name (highest priority)
        result = self._search_index(self._platform_index, words, bigrams, trigrams)
        if result:
            return result

        # Priority 2: General keywords
        result = self._search_index(self._keyword_index, words, bigrams, trigrams)
        return result

    def _llm_route(self, query):
        """
        SLOW PATH: Use AI to classify ambiguous queries (~300-500ms).
        Only called when keywords can't decide.
        """
        brain = self.brain.get_brain(task_type="router")
        if not brain:
            logger.error("No brain available for routing")
            return "GENERAL"

        try:
            response = brain.invoke(self.ROUTING_PROMPT.format(query=query))
            route = response.content.strip().upper()

            for key in self.KEYWORD_RULES:
                if key in route:
                    return key

            return "GENERAL"
        except Exception as e:
            logger.error(f"Routing failed: {e}")
            return "GENERAL"

    def route_query(self, query):
        """
        Smart 2-layer routing:
          Layer 1: Keyword match (instant, free)
          Layer 2: LLM classification (only if keywords fail)
        """
        # Try fast keyword match first
        route = self._keyword_route(query)
        if route:
            return route, "keyword"

        # Fall back to LLM for ambiguous queries
        route = self._llm_route(query)
        return route, "llm"

    def run(self, query):
        """
        Main entry point. Routes query to the right agent and executes.
        Returns: {route, result, agent_used, routed_by}
        """
        route, routed_by = self.route_query(query)

        result = {
            "route": route,
            "routed_by": routed_by,
            "query": query,
            "agent_used": None,
            "result": None,
        }

        if route == "GMAIL":
            result["agent_used"] = "GmailAgent"
            result["result"] = self._handle_gmail(query)

        elif route == "SLACK":
            result["agent_used"] = "SlackAgent"
            result["result"] = self._handle_slack(query)

        elif route == "OUTLOOK":
            result["agent_used"] = "OutlookAgent"
            result["result"] = self._handle_outlook(query)

        else:
            result["agent_used"] = "Supervisor (direct)"
            result["result"] = self._handle_general(query)

        return result

    # ==================== HANDLERS ====================

    def _handle_gmail(self, query):
        """Route Gmail-related queries to the Gmail agent."""
        query_lower = query.lower()

        if any(w in query_lower for w in ["unread", "inbox", "fetch", "list"]):
            return self.gmail.fetch_emails(query="is:unread", max_results=10)

        if any(w in query_lower for w in ["search", "find email", "look for"]):
            # Extract search term (everything after the keyword)
            return self.gmail.fetch_emails(query=query, max_results=10)

        if any(w in query_lower for w in ["draft", "compose", "write"]):
            return {"action": "draft_email", "status": "need_details",
                    "message": "Please provide: to, subject, and body"}

        if any(w in query_lower for w in ["send"]):
            return {"action": "send_email", "status": "need_details",
                    "message": "Please provide: to, subject, and body"}

        if any(w in query_lower for w in ["summarize", "summary", "tldr"]):
            return {"action": "summarize_email", "status": "need_details",
                    "message": "Please provide the message_id to summarize"}

        # Default: fetch recent emails
        return self.gmail.fetch_emails(query="is:unread", max_results=5)

    def _handle_slack(self, query):
        """Route Slack-related queries to the Slack agent."""
        query_lower = query.lower()

        if any(w in query_lower for w in ["send", "post", "message"]):
            return {"action": "send_message", "status": "need_details",
                    "message": "Please provide: channel and text"}

        if any(w in query_lower for w in ["search", "find message"]):
            return self.slack.search_messages(query)

        if any(w in query_lower for w in ["history", "recent", "conversation"]):
            return {"action": "get_history", "status": "need_details",
                    "message": "Please provide the channel name or ID"}

        if any(w in query_lower for w in ["channel", "find channel"]):
            return self.slack.find_channel(query)

        if any(w in query_lower for w in ["user", "find user", "who"]):
            return self.slack.find_user(query)

        if any(w in query_lower for w in ["summarize", "summary"]):
            return {"action": "summarize_channel", "status": "need_details",
                    "message": "Please provide the channel name or ID"}

        return {"action": "slack_general", "status": "need_details",
                "message": "What would you like to do on Slack?"}

    def _handle_outlook(self, query):
        """Route Outlook-related queries to the Outlook agent."""
        query_lower = query.lower()

        if any(w in query_lower for w in ["inbox", "email", "messages", "unread"]):
            return self.outlook.list_messages(folder="inbox", top=10)

        if any(w in query_lower for w in ["calendar", "events", "schedule", "upcoming"]):
            return self.outlook.list_events(top=10)

        if any(w in query_lower for w in ["meeting", "create event", "book"]):
            return {"action": "create_event", "status": "need_details",
                    "message": "Please provide: subject, start, end, and attendees"}

        if any(w in query_lower for w in ["send", "compose", "draft"]):
            return {"action": "draft_email", "status": "need_details",
                    "message": "Please provide: to, subject, and body"}

        if any(w in query_lower for w in ["search"]):
            return self.outlook.search_messages(query)

        # Default: show inbox
        return self.outlook.list_messages(folder="inbox", top=5)

    def _handle_general(self, query):
        """Handle general queries using AI directly."""
        brain = self.brain.get_brain(task_type="general")
        if brain:
            try:
                response = brain.invoke(
                    f"You are a corporate assistant. Answer this briefly:\n{query}"
                )
                return response.content
            except Exception as e:
                return f"Error: {e}"
        return "No AI brain available. Check your API keys."

    # ==================== STATUS ====================

    def status(self):
        """Get system status."""
        return {
            "supervisor": "active",
            "brain": self.brain.status(),
            "agents": {
                "gmail": self._gmail is not None,
                "slack": self._slack is not None,
                "outlook": self._outlook is not None,
            },
        }


# --- TEST ---
if __name__ == "__main__":
    print("Supervisor Agent Test")
    print("=" * 50)
    agent = SupervisorAgent(user_id="default")
    print(f"Brain: {agent.brain.status()}")
    print(f"Status: {agent.status()}")

    # Test routing
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
        route, method = agent.route_query(q)
        print(f"  [{route:8s}] ({method:7s}) {q}")

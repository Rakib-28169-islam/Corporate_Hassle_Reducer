"""
=============================================================================
GMAIL AGENT — Email Specialist (extends BaseAgent)
=============================================================================

WHY THIS EXISTS:
    Handles ALL Gmail-related operations for the user.
    Inherits shared logic from BaseAgent (search, calculate, compose, summarize).
    Only overrides what's UNIQUE to Gmail:
        - _composio_search() -> fetches emails from Gmail API
        - _do_action()       -> sends, deletes, labels, replies via Gmail API
        - _get_data_type()   -> returns "email"

WHAT IT INHERITS (from BaseAgent):
    execute()       -> runs full operation pipeline
    _do_search()    -> SQLite -> ChromaDB -> Composio (smart threshold)
    _do_calculate() -> Python math first, LLM last resort
    _do_compose()   -> LLM generates text (draft emails)
    _do_summarize() -> LLM summarizes emails
    _do_chat()      -> general conversation

WHAT IT OVERRIDES (Gmail-specific):
    _composio_search() -> GMAIL_FETCH_EMAILS via Composio
    _do_action()       -> send, reply, draft, label, trash, delete
    _get_data_type()   -> "email"

COMPOSIO TOOLS USED (8 core tools):
    READ:    GMAIL_FETCH_EMAILS, GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID, GMAIL_GET_ATTACHMENT
    WRITE:   GMAIL_SEND_EMAIL, GMAIL_CREATE_EMAIL_DRAFT, GMAIL_REPLY_TO_THREAD, GMAIL_SEND_DRAFT
    MANAGE:  GMAIL_ADD_LABEL_TO_EMAIL, GMAIL_MOVE_TO_TRASH, GMAIL_DELETE_MESSAGE

USAGE:
    agent = GmailAgent(user_id="user_1")
    result = await agent.execute("show my unread emails")
    result = await agent.execute("how many emails from John?")
    result = await agent.execute("send email to boss about the meeting")

=============================================================================
"""

import sys
import os
import logging

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import BaseAgent
from tools.gmail_tools import GmailToolManager

logger = logging.getLogger(__name__)


class GmailAgent(BaseAgent):
    """
    Gmail Specialist Agent — inherits from BaseAgent.

    Handles email reading, sending, drafting, organizing, and searching.
    Uses Composio Gmail API for external calls, local cache for repeat queries.
    """

    def __init__(self, user_id="default"):
        """
        Initialize GmailAgent with Gmail-specific tools.

        Args:
            user_id (str): The user this agent works for.
                           Passed to both BaseAgent and GmailToolManager.
        """
        super().__init__(user_id=user_id, tool_name="gmail")
        self.tools = GmailToolManager(user_id=user_id)

    # =========================================================================
    # OVERRIDE: Composio Search (Gmail-specific)
    # =========================================================================

    def _composio_search(self, query):
        """
        Fetch emails from Gmail via Composio API.

        Called by BaseAgent._do_search() when local cache is empty or stale.

        How it works:
            1. Uses GMAIL_FETCH_EMAILS with the user's search query
            2. Gmail supports search operators:
               "is:unread" — unread emails
               "from:john" — emails from John
               "subject:meeting" — emails about meetings
               "newer_than:1d" — emails from last 24 hours

        Args:
            query (str): The search query (natural language or Gmail search syntax)

        Returns:
            Raw Composio API response (list of email dicts)
        """
        return self.tools.execute("GMAIL_FETCH_EMAILS", {
            "query": query,
            "max_results": 20,
        })

    def _get_data_type(self):
        """Return 'email' — the data type for Gmail cached items."""
        return "email"

    # =========================================================================
    # OVERRIDE: Actions (Gmail-specific mutations)
    # =========================================================================

    async def _do_action(self, query, context):
        """
        Handle Gmail mutations: send, reply, draft, label, trash, delete.

        How it works:
            1. Detect which action the user wants (keyword matching)
            2. Check if we have enough info to perform it
            3. If yes -> execute via Composio API
            4. If no  -> ask user for missing details
            5. Notify SyncService to invalidate cache

        Why ask for details?
            "send email" is not enough — we need: to, subject, body.
            The agent returns a "need_details" response so the frontend
            can prompt the user for the missing fields.

        Args:
            query   (str):  The user's query
            context (dict): May contain data from previous steps
                            (e.g., search results for "reply to last email")

        Returns:
            dict with: action (str), status (str), message (str), data (any)
        """
        query_lower = query.lower()

        # --- SEND EMAIL ---
        if any(w in query_lower for w in ["send email", "send mail",
                                           "send to", "email to"]):
            # Check if context has composed content
            compose_content = context.get("compose_content")
            if compose_content:
                # TODO: Extract 'to' from query or context
                return {
                    "action": "send_email",
                    "status": "need_details",
                    "message": "Draft is ready. Please provide: to (email address)",
                    "draft": compose_content,
                }

            return {
                "action": "send_email",
                "status": "need_details",
                "message": "To send an email, please provide: to, subject, and body",
            }

        # --- REPLY ---
        if any(w in query_lower for w in ["reply", "respond to"]):
            search_data = context.get("search_results", [])
            if search_data:
                return {
                    "action": "reply_to_thread",
                    "status": "need_details",
                    "message": "Reply context found. Please provide: reply body",
                    "original_email": search_data[0] if search_data else None,
                }
            return {
                "action": "reply_to_thread",
                "status": "need_details",
                "message": "Please provide: thread_id and reply body",
            }

        # --- DRAFT ---
        if any(w in query_lower for w in ["draft", "create draft", "save draft"]):
            return {
                "action": "draft_email",
                "status": "need_details",
                "message": "To create a draft, please provide: to, subject, and body",
            }

        # --- LABEL ---
        if any(w in query_lower for w in ["label", "add label", "star",
                                           "mark as", "important"]):
            return {
                "action": "add_label",
                "status": "need_details",
                "message": "Please provide: message_id and label name",
            }

        # --- TRASH ---
        if any(w in query_lower for w in ["trash", "move to trash", "discard"]):
            return {
                "action": "move_to_trash",
                "status": "need_details",
                "message": "Please provide: message_id to move to trash",
            }

        # --- DELETE ---
        if any(w in query_lower for w in ["delete", "remove",
                                           "delete permanently"]):
            return {
                "action": "delete_message",
                "status": "need_details",
                "message": "Please provide: message_id to delete permanently",
            }

        # --- FORWARD ---
        if "forward" in query_lower:
            return {
                "action": "forward_email",
                "status": "need_details",
                "message": "Please provide: message_id and recipient email",
            }

        # --- DEFAULT ---
        await self._notify_action_complete("GMAIL_ACTION")
        return {
            "action": "gmail_action",
            "status": "need_details",
            "message": "What Gmail action would you like to perform? "
                       "(send, reply, draft, label, trash, delete)",
        }

    # =========================================================================
    # DIRECT TOOL METHODS (for programmatic access)
    # =========================================================================
    # These methods provide direct access to Composio tools
    # without going through the operation pipeline.
    # Used when the caller already knows exactly what to do.

    def fetch_emails(self, query="is:unread", max_results=10):
        """Fetch emails matching a Gmail search query."""
        return self.tools.execute("GMAIL_FETCH_EMAILS", {
            "query": query,
            "max_results": max_results,
        })

    def read_email(self, message_id):
        """Read a specific email by message ID."""
        return self.tools.execute("GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", {
            "message_id": message_id,
        })

    def get_attachment(self, message_id, attachment_id):
        """Download an email attachment."""
        return self.tools.execute("GMAIL_GET_ATTACHMENT", {
            "message_id": message_id,
            "attachment_id": attachment_id,
        })

    def draft_email(self, to, subject, body):
        """Create an email draft (doesn't send)."""
        return self.tools.execute("GMAIL_CREATE_EMAIL_DRAFT", {
            "to": to,
            "subject": subject,
            "body": body,
        })

    def send_email(self, to, subject, body):
        """Send an email directly."""
        return self.tools.execute("GMAIL_SEND_EMAIL", {
            "to": to,
            "subject": subject,
            "body": body,
        })

    def reply_to_thread(self, thread_id, body):
        """Reply to an existing email thread."""
        return self.tools.execute("GMAIL_REPLY_TO_THREAD", {
            "thread_id": thread_id,
            "body": body,
        })

    def send_draft(self, draft_id):
        """Send a previously created draft."""
        return self.tools.execute("GMAIL_SEND_DRAFT", {
            "draft_id": draft_id,
        })

    def add_label(self, message_id, label_ids):
        """Add labels to an email."""
        return self.tools.execute("GMAIL_ADD_LABEL_TO_EMAIL", {
            "message_id": message_id,
            "add_label_ids": label_ids,
        })

    def move_to_trash(self, message_id):
        """Move an email to trash (recoverable 30 days)."""
        return self.tools.execute("GMAIL_MOVE_TO_TRASH", {
            "message_id": message_id,
        })

    def delete_permanently(self, message_id):
        """Permanently delete an email (not recoverable)."""
        return self.tools.execute("GMAIL_DELETE_MESSAGE", {
            "message_id": message_id,
        })


# --- TEST ---
if __name__ == "__main__":
    print("Gmail Agent Test (BaseAgent)")
    print("=" * 50)
    agent = GmailAgent(user_id="default")
    print(f"Tool: {agent.tool_name}")
    print(f"Data type: {agent._get_data_type()}")
    print(f"Brain: {agent.brain.status()}")
    print(f"Inherits from: {GmailAgent.__bases__}")
    print("\nMethods (inherited + own):")
    for method in sorted([m for m in dir(agent)
                          if not m.startswith('_')
                          and callable(getattr(agent, m))]):
        source = "BaseAgent" if hasattr(BaseAgent, method) else "GmailAgent"
        print(f"   [{source:10}] agent.{method}()")

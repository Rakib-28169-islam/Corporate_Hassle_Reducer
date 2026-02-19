"""
=============================================================================
OUTLOOK AGENT — Outlook & Calendar Specialist (extends BaseAgent)
=============================================================================

WHY THIS EXISTS:
    Handles ALL Outlook-related operations for the user.
    This includes BOTH email (Outlook Mail) AND calendar (Outlook Calendar).
    Inherits shared logic from BaseAgent (search, calculate, compose, summarize).
    Only overrides what's UNIQUE to Outlook:
        - _composio_search() -> fetches Outlook emails/events via Composio
        - _do_action()       -> sends emails, creates events, manages calendar
        - _get_data_type()   -> returns "email" or "event"

COMPOSIO TOOLS USED (8 core tools):
    EMAIL:    OUTLOOK_OUTLOOK_LIST_MESSAGES, OUTLOOK_OUTLOOK_GET_MESSAGE,
              OUTLOOK_OUTLOOK_SEARCH_MESSAGES, OUTLOOK_OUTLOOK_CREATE_DRAFT,
              OUTLOOK_OUTLOOK_SEND_EMAIL, OUTLOOK_OUTLOOK_REPLY_EMAIL
    CALENDAR: OUTLOOK_OUTLOOK_LIST_EVENTS, OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT

USAGE:
    agent = OutlookAgent(user_id="user_1")
    result = await agent.execute("show my upcoming meetings")
    result = await agent.execute("send outlook email to team")
    result = await agent.execute("schedule a meeting for tomorrow at 3pm")

=============================================================================
"""

import sys
import os
import logging

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import BaseAgent
from tools.outlook_tools import OutlookToolManager

logger = logging.getLogger(__name__)


class OutlookAgent(BaseAgent):
    """
    Outlook Specialist Agent — inherits from BaseAgent.

    Handles Outlook email AND calendar operations.
    Uses Composio Outlook API for external calls, local cache for repeat queries.
    """

    def __init__(self, user_id="default"):
        """
        Initialize OutlookAgent with Outlook-specific tools.

        Args:
            user_id (str): The user this agent works for.
        """
        super().__init__(user_id=user_id, tool_name="outlook")
        self.tools = OutlookToolManager(user_id=user_id)

    # =========================================================================
    # OVERRIDE: Composio Search (Outlook-specific)
    # =========================================================================

    def _composio_search(self, query):
        """
        Fetch emails/events from Outlook via Composio API.

        Called by BaseAgent._do_search() when local cache is empty or stale.

        Detects whether user wants emails or calendar events and calls
        the appropriate Composio tool.

        Args:
            query (str): The search query

        Returns:
            Raw Composio API response
        """
        query_lower = query.lower()

        # Calendar-related queries -> list events
        if any(w in query_lower for w in ["meeting", "calendar", "event",
                                           "schedule", "agenda", "upcoming",
                                           "appointment", "booking"]):
            return self.tools.execute("OUTLOOK_OUTLOOK_LIST_EVENTS", {
                "top": 20,
            })

        # Email-related queries -> search messages
        if any(w in query_lower for w in ["search", "find", "look for"]):
            return self.tools.execute("OUTLOOK_OUTLOOK_SEARCH_MESSAGES", {
                "search": query,
            })

        # Default -> list inbox messages
        return self.tools.execute("OUTLOOK_OUTLOOK_LIST_MESSAGES", {
            "folder": "inbox",
            "top": 20,
        })

    def _get_data_type(self):
        """Return 'email' — primary data type for Outlook cached items."""
        return "email"

    # =========================================================================
    # OVERRIDE: Actions (Outlook-specific mutations)
    # =========================================================================

    async def _do_action(self, query, context):
        """
        Handle Outlook mutations: send email, reply, create draft, create event.

        Args:
            query   (str):  The user's query
            context (dict): May contain data from previous steps

        Returns:
            dict with: action (str), status (str), message (str)
        """
        query_lower = query.lower()

        # --- SEND EMAIL ---
        if any(w in query_lower for w in ["send email", "send mail",
                                           "send to", "email to"]):
            compose_content = context.get("compose_content")
            if compose_content:
                return {
                    "action": "send_email",
                    "status": "need_details",
                    "message": "Draft is ready. Please provide: to (email address)",
                    "draft": compose_content,
                }
            return {
                "action": "send_email",
                "status": "need_details",
                "message": "To send an Outlook email, please provide: "
                           "to, subject, and body",
            }

        # --- REPLY ---
        if any(w in query_lower for w in ["reply", "respond to"]):
            return {
                "action": "reply_email",
                "status": "need_details",
                "message": "Please provide: message_id and reply body",
            }

        # --- DRAFT ---
        if any(w in query_lower for w in ["draft", "create draft"]):
            return {
                "action": "create_draft",
                "status": "need_details",
                "message": "To create a draft, please provide: "
                           "to, subject, and body",
            }

        # --- CREATE EVENT ---
        if any(w in query_lower for w in ["schedule", "create event",
                                           "book meeting", "set up meeting",
                                           "add to calendar"]):
            return {
                "action": "create_event",
                "status": "need_details",
                "message": "To schedule a meeting, please provide: "
                           "subject, start time, end time, and attendees",
            }

        # --- RESCHEDULE ---
        if any(w in query_lower for w in ["reschedule", "move meeting",
                                           "change time"]):
            return {
                "action": "reschedule_event",
                "status": "need_details",
                "message": "Please provide: event_id and new time",
            }

        # --- CANCEL EVENT ---
        if any(w in query_lower for w in ["cancel meeting", "cancel event",
                                           "remove event"]):
            return {
                "action": "cancel_event",
                "status": "need_details",
                "message": "Please provide: event_id to cancel",
            }

        # --- DEFAULT ---
        await self._notify_action_complete("OUTLOOK_ACTION")
        return {
            "action": "outlook_action",
            "status": "need_details",
            "message": "What Outlook action would you like to perform? "
                       "(send email, reply, draft, schedule meeting, "
                       "reschedule, cancel)",
        }

    # =========================================================================
    # DIRECT TOOL METHODS (for programmatic access)
    # =========================================================================

    def list_messages(self, folder="inbox", top=10):
        """List recent emails from an Outlook folder."""
        return self.tools.execute("OUTLOOK_OUTLOOK_LIST_MESSAGES", {
            "folder": folder,
            "top": top,
        })

    def read_message(self, message_id):
        """Read a specific Outlook email by ID."""
        return self.tools.execute("OUTLOOK_OUTLOOK_GET_MESSAGE", {
            "message_id": message_id,
        })

    def search_messages(self, query):
        """Search Outlook emails by keyword."""
        return self.tools.execute("OUTLOOK_OUTLOOK_SEARCH_MESSAGES", {
            "search": query,
        })

    def draft_email(self, to, subject, body):
        """Create an Outlook email draft."""
        return self.tools.execute("OUTLOOK_OUTLOOK_CREATE_DRAFT", {
            "to_recipients": to,
            "subject": subject,
            "body": body,
        })

    def send_email(self, to, subject, body):
        """Send an Outlook email directly."""
        return self.tools.execute("OUTLOOK_OUTLOOK_SEND_EMAIL", {
            "to_recipients": to,
            "subject": subject,
            "body": body,
        })

    def reply_email(self, message_id, body):
        """Reply to an existing Outlook email."""
        return self.tools.execute("OUTLOOK_OUTLOOK_REPLY_EMAIL", {
            "message_id": message_id,
            "comment": body,
        })

    def list_events(self, top=10):
        """List upcoming Outlook calendar events."""
        return self.tools.execute("OUTLOOK_OUTLOOK_LIST_EVENTS", {
            "top": top,
        })

    def create_event(self, subject, start, end, attendees=None):
        """Create an Outlook calendar event."""
        params = {
            "subject": subject,
            "start": start,
            "end": end,
        }
        if attendees:
            params["attendees"] = attendees
        return self.tools.execute(
            "OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT", params
        )


# --- TEST ---
if __name__ == "__main__":
    print("Outlook Agent Test (BaseAgent)")
    print("=" * 50)
    agent = OutlookAgent(user_id="default")
    print(f"Tool: {agent.tool_name}")
    print(f"Data type: {agent._get_data_type()}")
    print(f"Brain: {agent.brain.status()}")
    print(f"Inherits from: {OutlookAgent.__bases__}")
    print("\nMethods (inherited + own):")
    for method in sorted([m for m in dir(agent)
                          if not m.startswith('_')
                          and callable(getattr(agent, m))]):
        source = "BaseAgent" if hasattr(BaseAgent, method) else "OutlookAgent"
        print(f"   [{source:10}] agent.{method}()")

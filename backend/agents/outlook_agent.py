"""Outlook Agent — email & calendar specialist, extends BaseAgent."""

import logging

from agents.base_agent import BaseAgent
from tools.outlook_tools import OutlookToolManager

logger = logging.getLogger(__name__)


class OutlookAgent(BaseAgent):
    """Handles all Outlook operations. Overrides search, action, and data_type."""

    _ACTION_MAP = [
        (["send email", "send mail", "send to", "email to"],
         "send_email", "To send an Outlook email, please provide: to, subject, and body"),
        (["reply", "respond to"],
         "reply_email", "Please provide: message_id and reply body"),
        (["draft", "create draft"],
         "create_draft", "To create a draft, please provide: to, subject, and body"),
        (["schedule", "create event", "book meeting", "set up meeting", "add to calendar"],
         "create_event", "To schedule a meeting, please provide: "
                         "subject, start time, end time, and attendees"),
        (["reschedule", "move meeting", "change time"],
         "reschedule_event", "Please provide: event_id and new time"),
        (["cancel meeting", "cancel event", "remove event"],
         "cancel_event", "Please provide: event_id to cancel"),
    ]

    def __init__(self, user_id="default"):
        super().__init__(user_id=user_id, tool_name="outlook")
        self.tools = OutlookToolManager(user_id=user_id)

    def _composio_search(self, query):
        """Fetch emails/events from Outlook via Composio API."""
        query_lower = query.lower()

        if any(w in query_lower for w in ["meeting", "calendar", "event",
                                           "schedule", "agenda", "upcoming",
                                           "appointment", "booking"]):
            return self.tools.execute("OUTLOOK_OUTLOOK_LIST_EVENTS", {"top": 20})

        if any(w in query_lower for w in ["search", "find", "look for"]):
            return self.tools.execute("OUTLOOK_OUTLOOK_SEARCH_MESSAGES", {"search": query})

        return self.tools.execute("OUTLOOK_OUTLOOK_LIST_MESSAGES", {"folder": "inbox", "top": 20})

    def _get_data_type(self):
        return "email"

    async def _do_action(self, query, context):
        """Handle Outlook actions using keyword-driven dispatch."""
        query_lower = query.lower()

        # Send email (with optional draft)
        if any(w in query_lower for w in ["send email", "send mail", "send to", "email to"]):
            compose_content = context.get("compose_content")
            if compose_content:
                return {
                    "action": "send_email", "status": "need_details",
                    "message": "Draft is ready. Please provide: to (email address)",
                    "draft": compose_content,
                }

        # Table-driven dispatch
        result = self._match_action(query_lower)
        if result:
            return result

        await self._notify_action_complete("OUTLOOK_ACTION")
        return {
            "action": "outlook_action", "status": "need_details",
            "message": "What Outlook action would you like to perform? "
                       "(send email, reply, draft, schedule meeting, reschedule, cancel)",
        }

    def _match_action(self, query_lower):
        """Match query against action keyword table."""
        for keywords, action, message in self._ACTION_MAP:
            if any(w in query_lower for w in keywords):
                return {"action": action, "status": "need_details", "message": message}
        return None

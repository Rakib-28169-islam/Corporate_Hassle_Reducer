"""Gmail Agent — email specialist, extends BaseAgent."""

import logging

from agents.base_agent import BaseAgent
from tools.gmail_tools import GmailToolManager

logger = logging.getLogger(__name__)


class GmailAgent(BaseAgent):
    """Handles all Gmail operations. Overrides search, action, and data_type."""

    # Action keyword map: keywords -> (action_name, message, extra_keys)
    _ACTION_MAP = [
        (["send email", "send mail", "send to", "email to"],
         "send_email", "To send an email, please provide: to, subject, and body"),
        (["reply", "respond to"],
         "reply_to_thread", "Please provide: thread_id and reply body"),
        (["draft", "create draft", "save draft"],
         "draft_email", "To create a draft, please provide: to, subject, and body"),
        (["label", "add label", "star", "mark as", "important"],
         "add_label", "Please provide: message_id and label name"),
        (["trash", "move to trash", "discard"],
         "move_to_trash", "Please provide: message_id to move to trash"),
        (["delete", "remove", "delete permanently"],
         "delete_message", "Please provide: message_id to delete permanently"),
        (["forward"],
         "forward_email", "Please provide: message_id and recipient email"),
    ]

    def __init__(self, user_id="default"):
        super().__init__(user_id=user_id, tool_name="gmail")
        self.tools = GmailToolManager(user_id=user_id)

    def _composio_search(self, query):
        """Fetch emails from Gmail via Composio API."""
        return self.tools.execute("GMAIL_FETCH_EMAILS", {
            "query": query, "max_results": 20,
        })

    def _get_data_type(self):
        return "email"

    async def _do_action(self, query, context):
        """Handle Gmail actions using keyword-driven dispatch."""
        query_lower = query.lower()

        # Check for draft-ready send
        if any(w in query_lower for w in ["send email", "send mail", "send to", "email to"]):
            compose_content = context.get("compose_content")
            if compose_content:
                return {
                    "action": "send_email", "status": "need_details",
                    "message": "Draft is ready. Please provide: to (email address)",
                    "draft": compose_content,
                }

        # Check for reply with context
        if any(w in query_lower for w in ["reply", "respond to"]):
            search_data = context.get("search_results", [])
            if search_data:
                return {
                    "action": "reply_to_thread", "status": "need_details",
                    "message": "Reply context found. Please provide: reply body",
                    "original_email": search_data[0] if search_data else None,
                }

        # Table-driven dispatch for remaining actions
        result = self._match_action(query_lower)
        if result:
            return result

        await self._notify_action_complete("GMAIL_ACTION")
        return {
            "action": "gmail_action", "status": "need_details",
            "message": "What Gmail action would you like to perform? "
                       "(send, reply, draft, label, trash, delete)",
        }

    def _match_action(self, query_lower):
        """Match query against action keyword table."""
        for keywords, action, message in self._ACTION_MAP:
            if any(w in query_lower for w in keywords):
                return {"action": action, "status": "need_details", "message": message}
        return None

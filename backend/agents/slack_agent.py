"""Slack Agent — messaging specialist, extends BaseAgent."""

import logging

from agents.base_agent import BaseAgent
from tools.slack_tools import SlackToolManager

logger = logging.getLogger(__name__)


class SlackAgent(BaseAgent):
    """Handles all Slack operations. Overrides search, action, and data_type."""

    _ACTION_MAP = [
        (["react", "emoji", "thumbs up"],
         "add_reaction", "Please provide: channel, message timestamp, and emoji name"),
        (["find channel", "search channel", "which channel"],
         "find_channel", "Please provide: channel name to search for"),
        (["find user", "search user", "who is"],
         "find_user", "Please provide: user name to search for"),
        (["pin", "bookmark"],
         "pin_message", "Please provide: channel and message to pin"),
    ]

    def __init__(self, user_id="default"):
        super().__init__(user_id=user_id, tool_name="slack")
        self.tools = SlackToolManager(user_id=user_id)

    def _composio_search(self, query):
        """Search Slack messages via Composio API."""
        return self.tools.execute("SLACK_SEARCH_MESSAGES", {"query": query})

    def _get_data_type(self):
        return "message"

    async def _do_action(self, query, context):
        """Handle Slack actions using keyword-driven dispatch."""
        query_lower = query.lower()

        # Send message (with optional draft)
        if any(w in query_lower for w in ["send", "post", "message to", "write to", "tell"]):
            compose_content = context.get("compose_content")
            if compose_content:
                return {
                    "action": "send_message", "status": "need_details",
                    "message": "Message drafted. Please provide: channel name",
                    "draft": compose_content,
                }
            return {
                "action": "send_message", "status": "need_details",
                "message": "To send a Slack message, please provide: channel and message text",
            }

        # Table-driven dispatch
        result = self._match_action(query_lower)
        if result:
            return result

        await self._notify_action_complete("SLACK_ACTION")
        return {
            "action": "slack_action", "status": "need_details",
            "message": "What Slack action would you like to perform? "
                       "(send message, react, find channel/user)",
        }

    def _match_action(self, query_lower):
        """Match query against action keyword table."""
        for keywords, action, message in self._ACTION_MAP:
            if any(w in query_lower for w in keywords):
                return {"action": action, "status": "need_details", "message": message}
        return None

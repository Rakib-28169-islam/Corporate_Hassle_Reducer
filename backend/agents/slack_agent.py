"""
=============================================================================
SLACK AGENT — Messaging Specialist (extends BaseAgent)
=============================================================================

WHY THIS EXISTS:
    Handles ALL Slack-related operations for the user.
    Inherits shared logic from BaseAgent (search, calculate, compose, summarize).
    Only overrides what's UNIQUE to Slack:
        - _composio_search() -> searches Slack messages via Composio
        - _do_action()       -> posts messages, reacts, finds channels/users
        - _get_data_type()   -> returns "message"

COMPOSIO TOOLS USED (6 core tools):
    MESSAGE:  SLACK_CHAT_POST_MESSAGE, SLACK_SEARCH_MESSAGES,
              SLACK_FETCH_CONVERSATION_HISTORY
    REACT:    SLACK_ADD_REACTION_TO_AN_ITEM
    DISCOVER: SLACK_FIND_CHANNELS, SLACK_FIND_USERS

USAGE:
    agent = SlackAgent(user_id="user_1")
    result = await agent.execute("search slack for project updates")
    result = await agent.execute("summarize #general channel")
    result = await agent.execute("send message to #dev about deployment")

=============================================================================
"""

import sys
import os
import logging

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import BaseAgent
from tools.slack_tools import SlackToolManager

logger = logging.getLogger(__name__)


class SlackAgent(BaseAgent):
    """
    Slack Specialist Agent — inherits from BaseAgent.

    Handles message sending, searching, channel discovery, and reactions.
    Uses Composio Slack API for external calls, local cache for repeat queries.
    """

    def __init__(self, user_id="default"):
        """
        Initialize SlackAgent with Slack-specific tools.

        Args:
            user_id (str): The user this agent works for.
        """
        super().__init__(user_id=user_id, tool_name="slack")
        self.tools = SlackToolManager(user_id=user_id)

    # =========================================================================
    # OVERRIDE: Composio Search (Slack-specific)
    # =========================================================================

    def _composio_search(self, query):
        """
        Search Slack messages via Composio API.

        Called by BaseAgent._do_search() when local cache is empty or stale.

        Args:
            query (str): The search query

        Returns:
            Raw Composio API response (list of message dicts)
        """
        return self.tools.execute("SLACK_SEARCH_MESSAGES", {
            "query": query,
        })

    def _get_data_type(self):
        """Return 'message' — the data type for Slack cached items."""
        return "message"

    # =========================================================================
    # OVERRIDE: Actions (Slack-specific mutations)
    # =========================================================================

    async def _do_action(self, query, context):
        """
        Handle Slack mutations: send message, react, find channel/user.

        Args:
            query   (str):  The user's query
            context (dict): May contain data from previous steps

        Returns:
            dict with: action (str), status (str), message (str)
        """
        query_lower = query.lower()

        # --- SEND MESSAGE ---
        if any(w in query_lower for w in ["send", "post", "message to",
                                           "write to", "tell"]):
            compose_content = context.get("compose_content")
            if compose_content:
                return {
                    "action": "send_message",
                    "status": "need_details",
                    "message": "Message drafted. Please provide: channel name",
                    "draft": compose_content,
                }
            return {
                "action": "send_message",
                "status": "need_details",
                "message": "To send a Slack message, please provide: "
                           "channel and message text",
            }

        # --- REACT ---
        if any(w in query_lower for w in ["react", "emoji", "thumbs up"]):
            return {
                "action": "add_reaction",
                "status": "need_details",
                "message": "Please provide: channel, message timestamp, "
                           "and emoji name",
            }

        # --- FIND CHANNEL ---
        if any(w in query_lower for w in ["find channel", "search channel",
                                           "which channel"]):
            return {
                "action": "find_channel",
                "status": "need_details",
                "message": "Please provide: channel name to search for",
            }

        # --- FIND USER ---
        if any(w in query_lower for w in ["find user", "search user",
                                           "who is"]):
            return {
                "action": "find_user",
                "status": "need_details",
                "message": "Please provide: user name to search for",
            }

        # --- PIN ---
        if any(w in query_lower for w in ["pin", "bookmark"]):
            return {
                "action": "pin_message",
                "status": "need_details",
                "message": "Please provide: channel and message to pin",
            }

        # --- DEFAULT ---
        await self._notify_action_complete("SLACK_ACTION")
        return {
            "action": "slack_action",
            "status": "need_details",
            "message": "What Slack action would you like to perform? "
                       "(send message, react, find channel/user)",
        }

    # =========================================================================
    # DIRECT TOOL METHODS (for programmatic access)
    # =========================================================================

    def send_message(self, channel, text):
        """Send a message to a Slack channel."""
        return self.tools.execute("SLACK_CHAT_POST_MESSAGE", {
            "channel": channel,
            "text": text,
        })

    def search_messages(self, query):
        """Search messages across Slack."""
        return self.tools.execute("SLACK_SEARCH_MESSAGES", {
            "query": query,
        })

    def get_conversation_history(self, channel, limit=20):
        """Get recent messages from a channel."""
        return self.tools.execute("SLACK_FETCH_CONVERSATION_HISTORY", {
            "channel": channel,
            "limit": limit,
        })

    def react_to_message(self, channel, timestamp, emoji):
        """Add a reaction to a message."""
        return self.tools.execute("SLACK_ADD_REACTION_TO_AN_ITEM", {
            "channel": channel,
            "timestamp": timestamp,
            "name": emoji,
        })

    def find_channel(self, name):
        """Find a channel by name."""
        return self.tools.execute("SLACK_FIND_CHANNELS", {
            "query": name,
        })

    def find_user(self, name):
        """Find a user by name."""
        return self.tools.execute("SLACK_FIND_USERS", {
            "query": name,
        })


# --- TEST ---
if __name__ == "__main__":
    print("Slack Agent Test (BaseAgent)")
    print("=" * 50)
    agent = SlackAgent(user_id="default")
    print(f"Tool: {agent.tool_name}")
    print(f"Data type: {agent._get_data_type()}")
    print(f"Brain: {agent.brain.status()}")
    print(f"Inherits from: {SlackAgent.__bases__}")
    print("\nMethods (inherited + own):")
    for method in sorted([m for m in dir(agent)
                          if not m.startswith('_')
                          and callable(getattr(agent, m))]):
        source = "BaseAgent" if hasattr(BaseAgent, method) else "SlackAgent"
        print(f"   [{source:10}] agent.{method}()")

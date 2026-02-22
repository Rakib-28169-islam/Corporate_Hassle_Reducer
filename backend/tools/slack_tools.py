"""Slack tool manager — Composio SDK wrapper for Slack operations."""

import os
from composio_client import Composio
from dotenv import load_dotenv

load_dotenv()


class SlackToolManager:
    """Provides focused toolkits for the Slack Agent."""

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

    def get_agent_tools(self):
        """Core toolkit (6 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_CHAT_POST_MESSAGE",
            "SLACK_FIND_CHANNELS",
            "SLACK_FIND_USERS",
            "SLACK_FETCH_CONVERSATION_HISTORY",
            "SLACK_SEARCH_MESSAGES",
            "SLACK_ADD_REACTION_TO_AN_ITEM",
        ]))

    def get_notifier_tools(self):
        """Send notifications only (2 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_CHAT_POST_MESSAGE",
            "SLACK_FIND_CHANNELS",
        ]))

    def get_channel_tools(self):
        """Channel management (5 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_CREATE_CHANNEL",
            "SLACK_FIND_CHANNELS",
            "SLACK_LIST_ALL_CHANNELS",
            "SLACK_INVITE_USER_TO_CHANNEL",
            "SLACK_RETRIEVE_CONVERSATION_INFORMATION",
        ]))

    def get_user_tools(self):
        """User lookup tools (3 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_FIND_USERS",
            "SLACK_FIND_USER_BY_EMAIL_ADDRESS",
            "SLACK_RETRIEVE_DETAILED_USER_INFORMATION",
        ]))

    def get_file_tools(self):
        """File sharing tools (3 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_UPLOAD_OR_CREATE_A_FILE_IN_SLACK",
            "SLACK_LIST_FILES_WITH_FILTERS_IN_SLACK",
            "SLACK_RETRIEVE_DETAILED_INFORMATION_ABOUT_A_FILE",
        ]))

    def get_all_tools(self):
        """All Slack tools — for debugging only."""
        return self.client.tools.list(toolkit_slug="slack", limit=200)

    def execute(self, action_name, params):
        """Execute a Slack tool directly."""
        return self.client.tools.execute(
            tool_slug=action_name, arguments=params, entity_id=self.user_id,
        )

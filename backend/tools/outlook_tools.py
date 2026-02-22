"""Outlook tool manager — Composio SDK wrapper for Outlook operations."""

import os
from composio_client import Composio
from dotenv import load_dotenv

load_dotenv()


class OutlookToolManager:
    """Provides focused toolkits for the Outlook Agent."""

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

    def get_agent_tools(self):
        """Core toolkit (8 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_MESSAGES",
            "OUTLOOK_OUTLOOK_GET_MESSAGE",
            "OUTLOOK_OUTLOOK_SEARCH_MESSAGES",
            "OUTLOOK_OUTLOOK_CREATE_DRAFT",
            "OUTLOOK_OUTLOOK_SEND_EMAIL",
            "OUTLOOK_OUTLOOK_REPLY_EMAIL",
            "OUTLOOK_OUTLOOK_LIST_EVENTS",
            "OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT",
        ]))

    def get_email_tools(self):
        """Email only (6 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_MESSAGES",
            "OUTLOOK_OUTLOOK_GET_MESSAGE",
            "OUTLOOK_OUTLOOK_SEARCH_MESSAGES",
            "OUTLOOK_OUTLOOK_CREATE_DRAFT",
            "OUTLOOK_OUTLOOK_SEND_EMAIL",
            "OUTLOOK_OUTLOOK_REPLY_EMAIL",
        ]))

    def get_calendar_tools(self):
        """Calendar only (4 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_EVENTS",
            "OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT",
            "OUTLOOK_OUTLOOK_GET_EVENT",
            "OUTLOOK_OUTLOOK_UPDATE_CALENDAR_EVENT",
        ]))

    def get_contact_tools(self):
        """Contact management (4 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_CONTACTS",
            "OUTLOOK_OUTLOOK_GET_CONTACT",
            "OUTLOOK_OUTLOOK_CREATE_CONTACT",
            "OUTLOOK_OUTLOOK_UPDATE_CONTACT",
        ]))

    def get_folder_tools(self):
        """Folder/organization (3 tools)."""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_MAIL_FOLDERS",
            "OUTLOOK_CREATE_MAIL_FOLDER",
            "OUTLOOK_OUTLOOK_MOVE_MESSAGE",
        ]))

    def get_all_tools(self):
        """All Outlook tools — for debugging only."""
        return self.client.tools.list(toolkit_slug="outlook", limit=100)

    def execute(self, action_name, params):
        """Execute an Outlook tool directly."""
        return self.client.tools.execute(
            tool_slug=action_name, arguments=params, entity_id=self.user_id,
        )

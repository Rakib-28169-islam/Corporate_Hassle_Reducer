import os
import sys
from composio_client import Composio
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()


class OutlookToolManager:
    """
    Outlook Specialist Tools.
    Provides focused toolkits for the Outlook Agent.
    """
    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

    # --- Agent Toolkit (8 core tools) ---
    def get_agent_tools(self):
        """Outlook Agent: Full focused toolkit"""
        return self.client.tools.list(tool_slugs=",".join([
            # Email
            "OUTLOOK_OUTLOOK_LIST_MESSAGES",
            "OUTLOOK_OUTLOOK_GET_MESSAGE",
            "OUTLOOK_OUTLOOK_SEARCH_MESSAGES",
            "OUTLOOK_OUTLOOK_CREATE_DRAFT",
            "OUTLOOK_OUTLOOK_SEND_EMAIL",
            "OUTLOOK_OUTLOOK_REPLY_EMAIL",
            # Calendar
            "OUTLOOK_OUTLOOK_LIST_EVENTS",
            "OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT",
        ]))

    # --- Sub-toolkits ---
    def get_email_tools(self):
        """Email only (6 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_MESSAGES",
            "OUTLOOK_OUTLOOK_GET_MESSAGE",
            "OUTLOOK_OUTLOOK_SEARCH_MESSAGES",
            "OUTLOOK_OUTLOOK_CREATE_DRAFT",
            "OUTLOOK_OUTLOOK_SEND_EMAIL",
            "OUTLOOK_OUTLOOK_REPLY_EMAIL",
        ]))

    def get_calendar_tools(self):
        """Calendar only (4 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_EVENTS",
            "OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT",
            "OUTLOOK_OUTLOOK_GET_EVENT",
            "OUTLOOK_OUTLOOK_UPDATE_CALENDAR_EVENT",
        ]))

    def get_contact_tools(self):
        """Contact management (4 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_CONTACTS",
            "OUTLOOK_OUTLOOK_GET_CONTACT",
            "OUTLOOK_OUTLOOK_CREATE_CONTACT",
            "OUTLOOK_OUTLOOK_UPDATE_CONTACT",
        ]))

    def get_folder_tools(self):
        """Folder/Organization (3 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "OUTLOOK_OUTLOOK_LIST_MAIL_FOLDERS",
            "OUTLOOK_CREATE_MAIL_FOLDER",
            "OUTLOOK_OUTLOOK_MOVE_MESSAGE",
        ]))

    # --- Full Toolkit (admin/debug) ---
    def get_all_tools(self):
        """All 43 Outlook tools - for debugging only"""
        return self.client.tools.list(toolkit_slug="outlook", limit=100)

    # --- Execute ---
    def execute(self, action_name, params):
        """Execute an Outlook tool directly."""
        return self.client.tools.execute(
            tool_slug=action_name, arguments=params, entity_id=self.user_id
        )


# --- TEST ---
if __name__ == "__main__":
    print("📨 Outlook Tools Test")
    print("=" * 50)
    manager = OutlookToolManager()

    agent_tools = manager.get_agent_tools()
    print(f"\nAgent Tools: {len(agent_tools.items)}")
    for t in agent_tools.items:
        print(f"   - {t.slug}")

    all_tools = manager.get_all_tools()
    print(f"\nAll Tools: {len(all_tools.items)}")

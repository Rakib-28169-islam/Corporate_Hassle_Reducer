import os
import sys
from composio_client import Composio
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()


class GmailToolManager:
    """
    Gmail Specialist Tools.
    Provides focused toolkits for the Email Agent.
    """
    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

    # --- Agent Toolkit (8 core tools) ---
    def get_agent_tools(self):
        """Email Agent: Full focused toolkit"""
        return self.client.tools.list(tool_slugs=",".join([
            "GMAIL_FETCH_EMAILS",
            "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
            "GMAIL_GET_ATTACHMENT",
            "GMAIL_CREATE_EMAIL_DRAFT",
            "GMAIL_SEND_EMAIL",
            "GMAIL_REPLY_TO_THREAD",
            "GMAIL_ADD_LABEL_TO_EMAIL",
            "GMAIL_MOVE_TO_TRASH",
        ]))

    # --- Sub-toolkits ---
    def get_reader_tools(self):
        """Read emails only (3 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "GMAIL_FETCH_EMAILS",
            "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
            "GMAIL_GET_ATTACHMENT",
        ]))

    def get_writer_tools(self):
        """Write/Send emails only (4 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "GMAIL_CREATE_EMAIL_DRAFT",
            "GMAIL_SEND_EMAIL",
            "GMAIL_REPLY_TO_THREAD",
            "GMAIL_SEND_DRAFT",
        ]))

    def get_organizer_tools(self):
        """Organize emails only (5 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "GMAIL_ADD_LABEL_TO_EMAIL",
            "GMAIL_CREATE_LABEL",
            "GMAIL_LIST_LABELS",
            "GMAIL_MOVE_TO_TRASH",
            "GMAIL_DELETE_MESSAGE",
        ]))

    def get_people_tools(self):
        """Contact/People tools (3 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "GMAIL_GET_CONTACTS",
            "GMAIL_SEARCH_PEOPLE",
            "GMAIL_GET_PEOPLE",
        ]))

    # --- Full Toolkit (admin/debug) ---
    def get_all_tools(self):
        """All 23 Gmail tools - for debugging only"""
        return self.client.tools.list(toolkit_slug="gmail", limit=100)

    # --- Execute ---
    def execute(self, action_name, params):
        """Execute a Gmail tool directly."""
        return self.client.tools.execute(
            tool_slug=action_name, arguments=params, entity_id=self.user_id
        )


# --- TEST ---
if __name__ == "__main__":
    print("📧 Gmail Tools Test")
    print("=" * 50)
    manager = GmailToolManager()

    agent_tools = manager.get_agent_tools()
    print(f"\nAgent Tools: {len(agent_tools.items)}")
    for t in agent_tools.items:
        print(f"   - {t.slug}")

    all_tools = manager.get_all_tools()
    print(f"\nAll Tools: {len(all_tools.items)}")

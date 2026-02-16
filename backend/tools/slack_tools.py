import os
import sys
from composio_client import Composio
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()


class SlackToolManager:
    """
    Slack Specialist Tools.
    Provides focused toolkits for the Slack Agent.
    """
    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

    # --- Agent Toolkit (6 core tools) ---
    def get_agent_tools(self):
        """Slack Agent: Full focused toolkit"""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_CHAT_POST_MESSAGE",
            "SLACK_FIND_CHANNELS",
            "SLACK_FIND_USERS",
            "SLACK_FETCH_CONVERSATION_HISTORY",
            "SLACK_SEARCH_MESSAGES",
            "SLACK_ADD_REACTION_TO_AN_ITEM",
        ]))

    # --- Sub-toolkits ---
    def get_notifier_tools(self):
        """Send notifications only (2 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_CHAT_POST_MESSAGE",
            "SLACK_FIND_CHANNELS",
        ]))

    def get_channel_tools(self):
        """Channel management (5 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_CREATE_CHANNEL",
            "SLACK_FIND_CHANNELS",
            "SLACK_LIST_ALL_CHANNELS",
            "SLACK_INVITE_USER_TO_CHANNEL",
            "SLACK_RETRIEVE_CONVERSATION_INFORMATION",
        ]))

    def get_user_tools(self):
        """User lookup tools (3 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_FIND_USERS",
            "SLACK_FIND_USER_BY_EMAIL_ADDRESS",
            "SLACK_RETRIEVE_DETAILED_USER_INFORMATION",
        ]))

    def get_file_tools(self):
        """File sharing tools (3 tools)"""
        return self.client.tools.list(tool_slugs=",".join([
            "SLACK_UPLOAD_OR_CREATE_A_FILE_IN_SLACK",
            "SLACK_LIST_FILES_WITH_FILTERS_IN_SLACK",
            "SLACK_RETRIEVE_DETAILED_INFORMATION_ABOUT_A_FILE",
        ]))

    # --- Full Toolkit (admin/debug) ---
    def get_all_tools(self):
        """All 133 Slack tools - for debugging only"""
        return self.client.tools.list(toolkit_slug="slack", limit=200)

    # --- Execute ---
    def execute(self, action_name, params):
        """Execute a Slack tool directly."""
        return self.client.tools.execute(
            tool_slug=action_name, arguments=params, entity_id=self.user_id
        )


# --- TEST ---
if __name__ == "__main__":
    print("💬 Slack Tools Test")
    print("=" * 50)
    manager = SlackToolManager()

    agent_tools = manager.get_agent_tools()
    print(f"\nAgent Tools: {len(agent_tools.items)}")
    for t in agent_tools.items:
        print(f"   - {t.slug}")

    all_tools = manager.get_all_tools()
    print(f"\nAll Tools: {len(all_tools.items)}")

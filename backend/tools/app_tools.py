import sys
sys.stdout.reconfigure(encoding='utf-8')

from tools.gmail_tools import GmailToolManager
from tools.slack_tools import SlackToolManager
from tools.outlook_tools import OutlookToolManager


class ToolManager:
    """
    Central Tool Manager.
    Creates platform-specific managers for each dedicated agent.

    Architecture:
        Supervisor --> EmailAgent   uses gmail.get_agent_tools()
                  --> SlackAgent   uses slack.get_agent_tools()
                  --> OutlookAgent uses outlook.get_agent_tools()
    """
    def __init__(self, user_id="default"):
        self.gmail = GmailToolManager(user_id=user_id)
        self.slack = SlackToolManager(user_id=user_id)
        self.outlook = OutlookToolManager(user_id=user_id)


# --- TEST ---
if __name__ == "__main__":
    print("🛠️  Tool Manager - Full Test")
    print("=" * 60)

    manager = ToolManager(user_id="default")

    # Email Agent
    email_tools = manager.gmail.get_agent_tools()
    print(f"\n📧 Email Agent: {len(email_tools.items)} tools")
    for t in email_tools.items:
        print(f"   - {t.slug}")

    # Slack Agent
    slack_tools = manager.slack.get_agent_tools()
    print(f"\n💬 Slack Agent: {len(slack_tools.items)} tools")
    for t in slack_tools.items:
        print(f"   - {t.slug}")

    # Outlook Agent
    outlook_tools = manager.outlook.get_agent_tools()
    print(f"\n📨 Outlook Agent: {len(outlook_tools.items)} tools")
    for t in outlook_tools.items:
        print(f"   - {t.slug}")

    total = len(email_tools.items) + len(slack_tools.items) + len(outlook_tools.items)
    print(f"\n{'=' * 60}")
    print(f"✅ TOTAL: {total} focused tools across 3 agents")

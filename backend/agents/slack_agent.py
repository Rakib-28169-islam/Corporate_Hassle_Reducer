import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.slack_tools import SlackToolManager
from core.llm_manager import get_brain_router


class SlackAgent:
    """
    Slack Specialist Agent.
    Sends messages, searches, manages channels.
    Connected to: SlackToolManager (6 focused tools)
    Brain: Groq (fast, casual tone)
    """

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.tools = SlackToolManager(user_id=user_id)
        self.brain = get_brain_router()

    # ==================== MESSAGING ====================

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

    # ==================== DISCOVERY ====================

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

    # ==================== AI POWERED ====================

    def notify(self, channel, event_summary):
        """Use AI to craft a notification message and send it."""
        brain = self.brain.get_brain(task_type="fast")
        if brain:
            response = brain.invoke(
                f"Write a short, casual Slack notification for this event. "
                f"Keep it under 2 lines. Use 1-2 relevant emojis.\n\n"
                f"Event: {event_summary}"
            )
            return self.send_message(channel, response.content)
        return None

    def summarize_channel(self, channel, limit=50):
        """Use AI to summarize recent channel activity."""
        history = self.get_conversation_history(channel, limit=limit)
        brain = self.brain.get_brain(task_type="reading")
        if brain and history:
            response = brain.invoke(
                f"Summarize the key topics and decisions from these "
                f"Slack messages in bullet points:\n\n{history}"
            )
            return response.content
        return None


# --- TEST ---
if __name__ == "__main__":
    print("💬 Slack Agent Test")
    print("=" * 50)
    agent = SlackAgent(user_id="default")
    print(f"Tools: {type(agent.tools).__name__}")
    print(f"Brain: {type(agent.brain).__name__}")
    print("Methods:")
    for method in [m for m in dir(agent) if not m.startswith('_')
                   and callable(getattr(agent, m))]:
        print(f"   - agent.{method}()")

import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.outlook_tools import OutlookToolManager
from core.llm_manager import get_brain_router


class OutlookAgent:
    """
    Outlook Specialist Agent.
    Manages Outlook email, calendar, and contacts.
    Connected to: OutlookToolManager (8 focused tools)
    Brain: Gemini (reading) + Groq (fast tasks)
    """

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.tools = OutlookToolManager(user_id=user_id)
        self.brain = get_brain_router()

    # ==================== EMAIL READ ====================

    def list_messages(self, folder="inbox", top=10):
        """List recent emails from a folder."""
        return self.tools.execute("OUTLOOK_OUTLOOK_LIST_MESSAGES", {
            "folder": folder,
            "top": top,
        })

    def read_message(self, message_id):
        """Read a specific email by ID."""
        return self.tools.execute("OUTLOOK_OUTLOOK_GET_MESSAGE", {
            "message_id": message_id,
        })

    def search_messages(self, query):
        """Search emails by keyword."""
        return self.tools.execute("OUTLOOK_OUTLOOK_SEARCH_MESSAGES", {
            "search": query,
        })

    # ==================== EMAIL WRITE ====================

    def draft_email(self, to, subject, body):
        """Create a draft (safe - doesn't send)."""
        return self.tools.execute("OUTLOOK_OUTLOOK_CREATE_DRAFT", {
            "to_recipients": to,
            "subject": subject,
            "body": body,
        })

    def send_email(self, to, subject, body):
        """Send an email directly."""
        return self.tools.execute("OUTLOOK_OUTLOOK_SEND_EMAIL", {
            "to_recipients": to,
            "subject": subject,
            "body": body,
        })

    def reply_email(self, message_id, body):
        """Reply to an existing email."""
        return self.tools.execute("OUTLOOK_OUTLOOK_REPLY_EMAIL", {
            "message_id": message_id,
            "comment": body,
        })

    # ==================== CALENDAR ====================

    def list_events(self, top=10):
        """List upcoming calendar events."""
        return self.tools.execute("OUTLOOK_OUTLOOK_LIST_EVENTS", {
            "top": top,
        })

    def create_event(self, subject, start, end, attendees=None):
        """Create a calendar event."""
        params = {
            "subject": subject,
            "start": start,
            "end": end,
        }
        if attendees:
            params["attendees"] = attendees
        return self.tools.execute("OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT", params)

    # ==================== AI POWERED ====================

    def summarize_email(self, message_id):
        """Use AI to summarize an Outlook email."""
        email = self.read_message(message_id)
        brain = self.brain.get_brain(task_type="reading")
        if brain and email:
            response = brain.invoke(
                f"Summarize this email in 2-3 bullet points:\n\n{email}"
            )
            return response.content
        return None

    def draft_reply(self, message_id, instructions):
        """Use AI to draft a reply based on instructions."""
        email = self.read_message(message_id)
        brain = self.brain.get_brain(task_type="general")
        if brain and email:
            response = brain.invoke(
                f"Draft a professional reply to this email.\n"
                f"Instructions: {instructions}\n\n"
                f"Original email:\n{email}"
            )
            return response.content
        return None

    def schedule_from_email(self, message_id):
        """Use AI to extract meeting details from an email and create event."""
        email = self.read_message(message_id)
        brain = self.brain.get_brain(task_type="fast")
        if brain and email:
            response = brain.invoke(
                f"Extract meeting details from this email. Return JSON:\n"
                f"- subject: string\n"
                f"- start: ISO datetime\n"
                f"- end: ISO datetime\n"
                f"- attendees: list of emails\n\n"
                f"Email:\n{email}"
            )
            return response.content
        return None


# --- TEST ---
if __name__ == "__main__":
    print("📨 Outlook Agent Test")
    print("=" * 50)
    agent = OutlookAgent(user_id="default")
    print(f"Tools: {type(agent.tools).__name__}")
    print(f"Brain: {type(agent.brain).__name__}")
    print("Methods:")
    for method in [m for m in dir(agent) if not m.startswith('_')
                   and callable(getattr(agent, m))]:
        print(f"   - agent.{method}()")

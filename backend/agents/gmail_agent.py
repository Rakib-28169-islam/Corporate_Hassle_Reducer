import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.gmail_tools import GmailToolManager
from core.llm_manager import get_brain_router


class GmailAgent:
    """
    Email Specialist Agent.
    Reads, writes, organizes, and manages Gmail.
    Connected to: GmailToolManager (8 focused tools)
    Brain: Gemini (reading/vision) + Groq (fast tasks)
    """

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.tools = GmailToolManager(user_id=user_id)
        self.brain = get_brain_router()

    # ==================== READ ====================

    def fetch_emails(self, query="is:unread", max_results=10):
        """Fetch emails matching a query."""
        return self.tools.execute("GMAIL_FETCH_EMAILS", {
            "query": query,
            "max_results": max_results,
        })

    def read_email(self, message_id):
        """Read a specific email by ID."""
        return self.tools.execute("GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", {
            "message_id": message_id,
        })

    def get_attachment(self, message_id, attachment_id):
        """Download an email attachment."""
        return self.tools.execute("GMAIL_GET_ATTACHMENT", {
            "message_id": message_id,
            "attachment_id": attachment_id,
        })

    # ==================== WRITE ====================

    def draft_email(self, to, subject, body):
        """Create a draft (safe - doesn't send)."""
        return self.tools.execute("GMAIL_CREATE_EMAIL_DRAFT", {
            "to": to,
            "subject": subject,
            "body": body,
        })

    def send_email(self, to, subject, body):
        """Send an email directly."""
        return self.tools.execute("GMAIL_SEND_EMAIL", {
            "to": to,
            "subject": subject,
            "body": body,
        })

    def reply_to_thread(self, thread_id, body):
        """Reply to an existing email thread."""
        return self.tools.execute("GMAIL_REPLY_TO_THREAD", {
            "thread_id": thread_id,
            "body": body,
        })

    def send_draft(self, draft_id):
        """Send a previously created draft."""
        return self.tools.execute("GMAIL_SEND_DRAFT", {
            "draft_id": draft_id,
        })

    # ==================== ORGANIZE ====================

    def add_label(self, message_id, label_ids):
        """Add labels to an email."""
        return self.tools.execute("GMAIL_ADD_LABEL_TO_EMAIL", {
            "message_id": message_id,
            "add_label_ids": label_ids,
        })

    def move_to_trash(self, message_id):
        """Soft delete - move to trash (recoverable 30 days)."""
        return self.tools.execute("GMAIL_MOVE_TO_TRASH", {
            "message_id": message_id,
        })

    def delete_permanently(self, message_id):
        """Hard delete - gone forever."""
        return self.tools.execute("GMAIL_DELETE_MESSAGE", {
            "message_id": message_id,
        })

    # ==================== AI POWERED ====================

    def summarize_email(self, message_id):
        """Use AI to summarize an email."""
        email = self.read_email(message_id)
        brain = self.brain.get_brain(task_type="reading")
        if brain and email:
            response = brain.invoke(
                f"Summarize this email in 2-3 bullet points:\n\n{email}"
            )
            return response.content
        return None

    def draft_reply(self, message_id, instructions):
        """Use AI to draft a reply based on instructions."""
        email = self.read_email(message_id)
        brain = self.brain.get_brain(task_type="general")
        if brain and email:
            response = brain.invoke(
                f"Draft a professional reply to this email.\n"
                f"Instructions: {instructions}\n\n"
                f"Original email:\n{email}"
            )
            return response.content
        return None

    def classify_email(self, message_id):
        """Use AI to classify email priority and category."""
        email = self.read_email(message_id)
        brain = self.brain.get_brain(task_type="fast")
        if brain and email:
            response = brain.invoke(
                f"Classify this email. Return JSON with:\n"
                f"- priority: urgent/high/normal/low\n"
                f"- category: work/personal/spam/newsletter/finance\n"
                f"- action_needed: true/false\n\n"
                f"Email:\n{email}"
            )
            return response.content
        return None


# --- TEST ---
if __name__ == "__main__":
    print("📧 Gmail Agent Test")
    print("=" * 50)
    agent = GmailAgent(user_id="default")
    print(f"Tools: {type(agent.tools).__name__}")
    print(f"Brain: {type(agent.brain).__name__}")
    print("Methods:")
    for method in [m for m in dir(agent) if not m.startswith('_')
                   and callable(getattr(agent, m))]:
        print(f"   - agent.{method}()")

import os
import sys
import logging

sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

load_dotenv()

logger = logging.getLogger(__name__)

# Singleton instance - created once, shared by all agents
_brain_instance = None


class BrainRouter:
    """
    The Intelligence Center (Singleton).
    Routes tasks to the best available AI model (Gemini vs Groq).
    Created once, shared across all agents.
    """

    def __init__(self):
        self.google_key = os.getenv("GOOGLE_API_KEY")
        self.groq_key = os.getenv("GROQ_API_KEY")

        # 1. PRIMARY BRAIN: Gemini 1.5 Flash
        try:
            self.gemini = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=self.google_key,
                temperature=0.3,
                convert_system_message_to_human=True
            )
        except Exception as e:
            logger.warning(f"Gemini failed to load: {e}")
            self.gemini = None

        # 2. FAST BRAIN: Groq (Llama 3.3)
        try:
            self.groq = ChatGroq(
                model="llama-3.3-70b-versatile",
                api_key=self.groq_key,
                temperature=0
            )
        except Exception as e:
            logger.warning(f"Groq failed to load: {e}")
            self.groq = None

    def get_brain(self, task_type="general"):
        """Decides which model to use based on the task."""
        if task_type in ["vision", "reading"]:
            return self.gemini or self.groq
        if task_type in ["router", "fast"]:
            return self.groq or self.gemini
        return self.gemini or self.groq

    def status(self):
        """Returns which brains are available."""
        return {
            "gemini": self.gemini is not None,
            "groq": self.groq is not None,
        }


def get_brain_router():
    """Get the singleton BrainRouter instance. Created once, reused everywhere."""
    global _brain_instance
    if _brain_instance is None:
        _brain_instance = BrainRouter()
    return _brain_instance


# --- TEST BLOCK ---
if __name__ == "__main__":
    print("Testing BrainRouter...")
    router = get_brain_router()
    print(f"Status: {router.status()}")

    brain = router.get_brain(task_type="fast")
    if brain:
        try:
            response = brain.invoke("Say '2+2=?' if you can hear me.")
            print(f"AI Response: {response.content}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("No brains available! Check your .env file.")

import os
import sys

# Fix Windows terminal encoding for emojis
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
# from langchain_community.chat_models import ChatOllama # Uncomment if you have Ollama installed locally

# Load environment variables from .env
load_dotenv()

class BrainRouter:
    """
    The Intelligence Center.
    Routes tasks to the best available AI model (Gemini vs Groq).
    """

    def __init__(self):
        self.google_key = os.getenv("GOOGLE_API_KEY")
        self.groq_key = os.getenv("GROQ_API_KEY")

        # 1. PRIMARY BRAIN: Gemini 1.5 Flash
        # Usage: Reading emails, Vision, Complex reasoning
        try:
            self.gemini = ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                google_api_key=self.google_key,
                temperature=0.3, # Low creativity for accuracy
                convert_system_message_to_human=True
            )
            print("🟢 Brain: Gemini 1.5 Flash Loaded.")
        except Exception as e:
            print(f"⚠️ Brain: Gemini Failed to Load: {e}")
            self.gemini = None

        # 2. FAST BRAIN: Groq (Llama 3.3)
        # Usage: Routing, simple replies, JSON formatting
        try:
            self.groq = ChatGroq(
                model="llama-3.3-70b-versatile",
                api_key=self.groq_key,
                temperature=0
            )
            print("🟢 Brain: Groq (Llama 3.3 70B) Loaded.")
        except Exception as e:
            print(f"⚠️ Brain: Groq Failed to Load: {e}")
            self.groq = None

    def get_brain(self, task_type="general"):
        """
        Decides which model to use based on the task.
        """
        # TASK: VISION or BIG CONTEXT -> Must use Gemini
        if task_type in ["vision", "reading"]:
            if self.gemini:
                return self.gemini
            print("🔸 Warning: Gemini down, falling back to Groq (Vision features will fail).")
            return self.groq

        # TASK: ROUTING or SPEED -> Use Groq
        if task_type in ["router", "fast"]:
            if self.groq:
                return self.groq
            return self.gemini

        # DEFAULT: Prefer Gemini, fallback to Groq
        return self.gemini if self.gemini else self.groq

# --- TEST BLOCK ---
if __name__ == "__main__":
    # Test if keys are working
    router = BrainRouter()
    brain = router.get_brain(task_type="fast")

    if brain:
        print("\n🧠 Testing Brain...")
        try:
            response = brain.invoke("Say '2+2=?' if you can hear me.")
            print(f"🤖 AI Response: {response.content}")
        except Exception as e:
            print(f"❌ Error invoking brain: {e}")
    else:
        print("❌ No Brains Available! Check your .env file.")

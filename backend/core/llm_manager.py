"""BrainRouter — singleton LLM manager with automatic fallback."""

import os
import logging
import concurrent.futures

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

load_dotenv()

logger = logging.getLogger(__name__)

_brain_instance = None


class BrainRouter:
    """Routes tasks to the best available AI model (Gemini vs Groq)."""

    def __init__(self):
        self.google_key = os.getenv("GOOGLE_API_KEY")
        self.groq_key = os.getenv("GROQ_API_KEY")

        try:
            self.gemini = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=self.google_key,
                temperature=0.3,
                convert_system_message_to_human=True,
            )
        except Exception as e:
            logger.warning(f"Gemini failed to load: {e}")
            self.gemini = None

        try:
            self.groq = ChatGroq(
                model="llama-3.3-70b-versatile",
                api_key=self.groq_key,
                temperature=0,
            )
        except Exception as e:
            logger.warning(f"Groq failed to load: {e}")
            self.groq = None

    def get_brain(self, task_type="general"):
        """Pick the best LLM for a task type."""
        if task_type in ["vision", "reading"]:
            return self.gemini or self.groq
        if task_type in ["router", "fast"]:
            return self.groq or self.gemini
        return self.gemini or self.groq

    def _get_fallback_chain(self, task_type="general"):
        """Returns ordered list of LLMs to try (primary first, then fallback)."""
        if task_type in ["router", "fast"]:
            chain = [self.groq, self.gemini]
        else:
            chain = [self.gemini, self.groq]
        return [llm for llm in chain if llm is not None]

    def invoke_with_fallback(self, prompt, task_type="general", timeout=10):
        """Try primary LLM, fall back to secondary on failure. Returns content string."""
        chain = self._get_fallback_chain(task_type)
        if not chain:
            raise RuntimeError("No AI brains available. Check your API keys.")

        last_error = None
        for llm in chain:
            model_name = getattr(llm, "model", "unknown")
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(llm.invoke, prompt)
                response = future.result(timeout=timeout)
                return response.content
            except concurrent.futures.TimeoutError:
                logger.warning(f"LLM {model_name} timed out after {timeout}s, trying fallback")
                last_error = TimeoutError(f"{model_name} timed out after {timeout}s")
            except Exception as e:
                logger.warning(f"LLM {model_name} failed, trying fallback: {e}")
                last_error = e
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        raise last_error

    def invoke_structured(self, prompt, schema, task_type="general", timeout=15):
        """Call LLM with structured output via with_structured_output().

        Uses the same fallback chain as invoke_with_fallback().
        Returns a validated Pydantic model instance (the schema type).
        """
        chain = self._get_fallback_chain(task_type)
        if not chain:
            raise RuntimeError("No AI brains available. Check your API keys.")

        last_error = None
        for llm in chain:
            model_name = getattr(llm, "model", "unknown")
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                structured_llm = llm.with_structured_output(schema)
                future = executor.submit(structured_llm.invoke, prompt)
                result = future.result(timeout=timeout)

                if result is not None:
                    logger.info(f"Structured output from {model_name}: OK")
                    return result

                logger.warning(f"Structured output from {model_name}: returned None")
                last_error = ValueError(f"{model_name} returned None")
            except concurrent.futures.TimeoutError:
                logger.warning(
                    f"LLM {model_name} timed out after {timeout}s, trying fallback")
                last_error = TimeoutError(f"{model_name} timed out after {timeout}s")
            except Exception as e:
                logger.warning(
                    f"LLM {model_name} structured output failed: {e}, trying fallback")
                last_error = e
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        raise last_error

    def status(self):
        """Returns which brains are available."""
        return {
            "gemini": self.gemini is not None,
            "groq": self.groq is not None,
        }


def get_brain_router():
    """Get the singleton BrainRouter instance."""
    global _brain_instance
    if _brain_instance is None:
        _brain_instance = BrainRouter()
    return _brain_instance

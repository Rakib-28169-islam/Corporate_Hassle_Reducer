"""Operation classifier — keyword n-gram matching to detect SEARCH/CALCULATE/ACTION/COMPOSE/SUMMARIZE/CHAT."""

import logging

logger = logging.getLogger(__name__)


class OperationClassifier:
    """Classifies user queries into operation types using keyword n-gram matching."""

    OPERATION_KEYWORDS = {
        "SEARCH": [
            "show", "find", "search", "get", "fetch", "retrieve", "list",
            "display", "look up", "look for", "pull up", "bring up",
            "read", "open", "view", "check", "see", "give me",
            "unread", "recent", "latest", "new", "from", "last",
            "older", "between", "since", "before", "after",
            "inbox", "email from", "email about", "emails from",
            "messages from", "messages about", "messages in",
            "upcoming", "next meeting", "today meeting", "events on",
            "schedule for", "agenda for",
            "describe", "details", "content", "body",
        ],
        "CALCULATE": [
            "how many", "count", "number of", "total",
            "sum", "add up", "average", "calculate", "compute",
            "compare", "difference", "more than", "less than",
            "most", "least", "highest", "lowest", "top",
            "percentage", "percent", "ratio",
        ],
        "ACTION": [
            "send", "post", "create", "submit", "publish",
            "reply", "respond", "forward",
            "move", "archive", "star", "label", "mark as",
            "pin", "unpin",
            "delete", "remove", "trash", "discard",
            "book", "schedule", "reschedule", "cancel",
            "invite", "rsvp", "accept", "decline",
            "react", "emoji", "thumbs up",
        ],
        "COMPOSE": [
            "draft", "compose", "write", "prepare",
            "generate", "create draft", "write up",
            "template", "format", "word it",
            "professional", "formal", "casual",
        ],
        "SUMMARIZE": [
            "summarize", "summary", "brief", "overview",
            "recap", "highlights", "key points",
            "explain", "what happened", "break down",
            "tldr", "tl;dr", "in short",
            "digest", "rundown", "catch me up",
            "what did i miss", "whats new",
        ],
        "CHAT": [
            "hello", "hi", "hey", "good morning", "good afternoon",
            "good evening", "howdy", "sup", "yo",
            "help", "what can you do", "how does this work",
            "who are you", "what are you",
            "thanks", "thank you", "great", "awesome", "cool",
            "ok", "okay", "sure", "yes", "no", "bye", "goodbye",
        ],
    }

    DEPENDENCIES = {
        "CALCULATE": ["SEARCH"],
        "SUMMARIZE": ["SEARCH"],
    }

    def __init__(self):
        """Build n-gram hashmap for fast keyword matching."""
        self._index = {1: {}, 2: {}, 3: {}}

        for operation, keywords in self.OPERATION_KEYWORDS.items():
            for keyword in keywords:
                words = keyword.lower().strip().split()
                n = min(len(words), 3)
                key = " ".join(words[:n])
                self._index[n][key] = operation

        logger.info(f"OperationClassifier initialized: "
                    f"{sum(len(v) for v in self._index.values())} keyword rules")

    def classify(self, query):
        """Classify a query into operation types.
        Returns dict with operations, pipeline, and classified_by.
        """
        query_lower = query.lower().strip()
        words = query_lower.split()
        matched_ops = set()

        # Larger n-grams checked first for more specific matches
        for n in [3, 2, 1]:
            if n > len(words):
                continue
            for i in range(len(words) - n + 1):
                ngram = " ".join(words[i:i + n])
                if ngram in self._index[n]:
                    matched_ops.add(self._index[n][ngram])

        if not matched_ops:
            return {
                "operations": ["CHAT"],
                "pipeline": [{"step": 1, "op": "CHAT", "depends_on": []}],
                "classified_by": "default",
            }

        # Add implicit dependencies (CALCULATE/SUMMARIZE always need SEARCH)
        for op in list(matched_ops):
            if op in self.DEPENDENCIES:
                for dep in self.DEPENDENCIES[op]:
                    matched_ops.add(dep)

        # Remove CHAT when data operations are present (CHAT is redundant
        # when user wants search results, not a greeting)
        data_ops = {"SEARCH", "CALCULATE", "SUMMARIZE", "ACTION", "COMPOSE"}
        if matched_ops & data_ops and "CHAT" in matched_ops:
            matched_ops.discard("CHAT")

        operations = sorted(matched_ops, key=self._operation_priority)
        pipeline = self._build_pipeline(operations)

        return {
            "operations": operations,
            "pipeline": pipeline,
            "classified_by": "keywords",
        }

    def _build_pipeline(self, operations):
        """Build ordered execution pipeline respecting dependencies.
        Operations at the same step can run in parallel.
        """
        pipeline = []
        placed = set()
        step = 1
        max_iterations = len(operations) + 1

        iteration = 0
        while len(placed) < len(operations) and iteration < max_iterations:
            iteration += 1

            ready = []
            for op in operations:
                if op in placed:
                    continue
                deps = self.DEPENDENCIES.get(op, [])
                relevant_deps = [d for d in deps if d in operations]
                if all(d in placed for d in relevant_deps):
                    ready.append(op)

            if not ready:
                # Fail-safe: place remaining ops if dependency graph is broken
                for op in operations:
                    if op not in placed:
                        pipeline.append({
                            "step": step,
                            "op": op,
                            "depends_on": [],
                        })
                        placed.add(op)
                break

            for op in ready:
                deps = self.DEPENDENCIES.get(op, [])
                relevant_deps = [d for d in deps if d in operations]
                pipeline.append({
                    "step": step,
                    "op": op,
                    "depends_on": relevant_deps,
                })
                placed.add(op)

            step += 1

        return pipeline

    def _operation_priority(self, op):
        """Sort key for logical execution order (lower = runs first)."""
        order = {
            "SEARCH": 0,
            "CALCULATE": 1,
            "SUMMARIZE": 2,
            "COMPOSE": 3,
            "ACTION": 4,
            "CHAT": 5,
        }
        return order.get(op, 99)

    def get_data_source(self, operation):
        """Get recommended data source strategy for an operation.
        Returns dict with source, fallback list, and cache bool.
        """
        strategies = {
            "SEARCH": {
                "source": "sqlite",
                "fallback": ["chromadb", "composio"],
                "cache": True,
            },
            "CALCULATE": {
                "source": "python",
                "fallback": ["regex", "llm"],
                "cache": True,
            },
            "ACTION": {
                "source": "composio",
                "fallback": ["retry_once"],
                "cache": False,
            },
            "COMPOSE": {
                "source": "llm_groq",
                "fallback": ["llm_gemini"],
                "cache": False,
            },
            "SUMMARIZE": {
                "source": "llm_gemini",
                "fallback": ["llm_groq"],
                "cache": True,
            },
            "CHAT": {
                "source": "llm_groq",
                "fallback": ["llm_gemini"],
                "cache": False,
            },
        }
        return strategies.get(operation, {
            "source": "llm_groq",
            "fallback": ["llm_gemini"],
            "cache": False,
        })


_classifier_instance = None


def get_classifier():
    """Get the singleton OperationClassifier instance."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = OperationClassifier()
    return _classifier_instance

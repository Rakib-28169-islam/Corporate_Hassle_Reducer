"""
=============================================================================
OPERATION CLASSIFIER — Knows WHAT to do with a query
=============================================================================

WHY THIS EXISTS:
    The SupervisorAgent knows WHERE to route (Gmail/Slack/Outlook).
    But it doesn't know WHAT operation the user wants:
        "show my emails"     → SEARCH (read data)
        "send email to boss" → ACTION (do something)
        "how many emails?"   → SEARCH + CALCULATE (read + count)

    This classifier runs IN PARALLEL with agent routing:
        ┌─────────────────────────────────────┐
        │         User: "count my emails"     │
        │              │                      │
        │     ┌────────┴────────┐             │
        │     ▼                 ▼             │
        │  Agent Router      Op Classifier    │
        │  → "GMAIL"         → [SEARCH, CALC] │
        │     └────────┬────────┘             │
        │              ▼                      │
        │   GmailAgent.execute(                │
        │     operations=[SEARCH, CALCULATE]) │
        └─────────────────────────────────────┘

6 OPERATION TYPES:
    ┌─────────────┬────────────────────────────────┬──────────────────────┐
    │ Operation   │ What it does                   │ Data source          │
    ├─────────────┼────────────────────────────────┼──────────────────────┤
    │ SEARCH      │ Find/retrieve data             │ SQLite→ChromaDB→API  │
    │ CALCULATE   │ Count, sum, compare            │ Python math first    │
    │ ACTION      │ Send, create, delete           │ Always Composio API  │
    │ COMPOSE     │ Write/draft content            │ LLM (Groq→Gemini)   │
    │ SUMMARIZE   │ Summarize/explain data         │ LLM (Gemini→Groq)   │
    │ CHAT        │ Greeting, general talk         │ LLM (Groq→Gemini)   │
    └─────────────┴────────────────────────────────┴──────────────────────┘

DEPENDENCY RULES:
    Some operations DEPEND on others (must run in order):
    - CALCULATE needs SEARCH results first (can't count without data)
    - SUMMARIZE needs SEARCH results first (can't summarize without data)
    - COMPOSE may need SEARCH context (draft reply needs original email)
    - ACTION is independent (send email doesn't need prior search)
    - CHAT is independent (greeting needs nothing)

HOW IT WORKS:
    Same pattern as SupervisorAgent's keyword router:
    - Keyword hashmap with n-gram matching (O(W) where W = words in query)
    - No LLM call needed for 90% of queries
    - Fast: < 1ms for classification

USAGE:
    from core.operation_classifier import OperationClassifier

    classifier = OperationClassifier()
    result = classifier.classify("how many unread emails from boss?")
    # result = {
    #     "operations": ["SEARCH", "CALCULATE"],
    #     "pipeline": [
    #         {"step": 1, "op": "SEARCH", "depends_on": []},
    #         {"step": 2, "op": "CALCULATE", "depends_on": ["SEARCH"]},
    #     ],
    #     "classified_by": "keywords"
    # }

=============================================================================
"""

import logging

logger = logging.getLogger(__name__)


class OperationClassifier:
    """
    Classifies user queries into operation types using keyword matching.

    Same design as SupervisorAgent's keyword router:
        1. Build n-gram hashmap at init (one-time cost)
        2. On each query, scan words against hashmap (O(W))
        3. Return matched operations + dependency pipeline

    Operations:
        SEARCH    — retrieve/find data (emails, messages, events)
        CALCULATE — count, sum, compare, filter numbers
        ACTION    — send, create, delete, move (mutations)
        COMPOSE   — write, draft, generate text
        SUMMARIZE — summarize, explain, tldr
        CHAT      — greeting, general question, help
    """

    # =========================================================================
    # KEYWORD RULES — Each operation has trigger words
    # =========================================================================
    # These keywords indicate which operation the user wants.
    # Multiple operations can match (e.g., "how many emails" → SEARCH + CALCULATE)

    OPERATION_KEYWORDS = {
        "SEARCH": [
            # Retrieve / find
            "show", "find", "search", "get", "fetch", "retrieve", "list",
            "display", "look up", "look for", "pull up", "bring up",
            # Read
            "read", "open", "view", "check", "see",
            # Filter
            "unread", "recent", "latest", "new", "from",
            "older", "between", "since", "before", "after",
            # Email-specific
            "inbox", "email from", "email about", "emails from",
            "messages from", "messages about", "messages in",
            # Calendar-specific
            "upcoming", "next meeting", "today meeting", "events on",
            "schedule for", "agenda for",
        ],

        "CALCULATE": [
            # Count
            "how many", "count", "number of", "total",
            # Math
            "sum", "add up", "average", "calculate", "compute",
            # Compare
            "compare", "difference", "more than", "less than",
            "most", "least", "highest", "lowest", "top",
            # Percentage
            "percentage", "percent", "ratio",
        ],

        "ACTION": [
            # Send / create
            "send", "post", "create", "submit", "publish",
            # Reply
            "reply", "respond", "forward",
            # Modify
            "move", "archive", "star", "label", "mark as",
            "pin", "unpin",
            # Delete
            "delete", "remove", "trash", "discard",
            # Calendar actions
            "book", "schedule", "reschedule", "cancel",
            "invite", "rsvp", "accept", "decline",
            # Reactions
            "react", "emoji", "thumbs up",
        ],

        "COMPOSE": [
            # Write / draft
            "draft", "compose", "write", "prepare",
            "generate", "create draft", "write up",
            # Template
            "template", "format", "word it",
            "professional", "formal", "casual",
        ],

        "SUMMARIZE": [
            # Summarize
            "summarize", "summary", "brief", "overview",
            "recap", "highlights", "key points",
            # Explain
            "explain", "what happened", "break down",
            "tldr", "tl;dr", "in short",
            # Digest
            "digest", "rundown", "catch me up",
            "what did i miss", "whats new",
        ],

        "CHAT": [
            # Greetings
            "hello", "hi", "hey", "good morning", "good afternoon",
            "good evening", "howdy", "sup", "yo",
            # Help
            "help", "what can you do", "how does this work",
            "who are you", "what are you",
            # Thanks
            "thanks", "thank you", "great", "awesome", "cool",
            # General
            "ok", "okay", "sure", "yes", "no", "bye", "goodbye",
        ],
    }

    # =========================================================================
    # DEPENDENCY RULES — Which operations need results from others
    # =========================================================================
    # Key = operation that DEPENDS on value = list of operations it needs first
    #
    # Example: CALCULATE depends on SEARCH
    #   "how many emails from john?" → first SEARCH for emails, then CALCULATE count
    #
    # Operations NOT listed here are independent (can run immediately)

    DEPENDENCIES = {
        "CALCULATE": ["SEARCH"],     # Can't count without data
        "SUMMARIZE": ["SEARCH"],     # Can't summarize without data
    }

    # =========================================================================
    # INIT — Build keyword hashmap once
    # =========================================================================

    def __init__(self):
        """
        Build n-gram hashmap for fast keyword matching.

        How it works:
            Each keyword phrase is split into n-grams (1-word, 2-word, 3-word).
            Stored in a dict: {ngram_size: {phrase: operation}}

            "how many" → 2-gram → _index[2]["how many"] = "CALCULATE"
            "send"     → 1-gram → _index[1]["send"] = "ACTION"

            At query time, we scan the query's n-grams against this hashmap.
        """
        self._index = {1: {}, 2: {}, 3: {}}

        for operation, keywords in self.OPERATION_KEYWORDS.items():
            for keyword in keywords:
                words = keyword.lower().strip().split()
                n = min(len(words), 3)  # Max 3-gram
                key = " ".join(words[:n])
                self._index[n][key] = operation

        logger.info(f"OperationClassifier initialized: "
                    f"{sum(len(v) for v in self._index.values())} keyword rules")

    # =========================================================================
    # CLASSIFY — The main method
    # =========================================================================

    def classify(self, query):
        """
        Classify a user query into operation types.

        How it works:
            1. Normalize query (lowercase, strip)
            2. Generate all 1-grams, 2-grams, 3-grams from query
            3. Match against keyword hashmap
            4. Collect all matching operations (can be multiple)
            5. Build dependency pipeline (which ops run first)
            6. If nothing matched → default to CHAT

        Args:
            query (str): The user's raw query text

        Returns:
            dict with:
                operations  (list): Matched operation types, e.g. ["SEARCH", "CALCULATE"]
                pipeline    (list): Ordered execution steps with dependencies
                classified_by (str): "keywords" or "default"

        Example:
            result = classifier.classify("how many unread emails from boss?")
            # {
            #     "operations": ["SEARCH", "CALCULATE"],
            #     "pipeline": [
            #         {"step": 1, "op": "SEARCH", "depends_on": []},
            #         {"step": 2, "op": "CALCULATE", "depends_on": ["SEARCH"]},
            #     ],
            #     "classified_by": "keywords"
            # }

            result = classifier.classify("hello")
            # {
            #     "operations": ["CHAT"],
            #     "pipeline": [{"step": 1, "op": "CHAT", "depends_on": []}],
            #     "classified_by": "keywords"
            # }
        """
        query_lower = query.lower().strip()
        words = query_lower.split()
        matched_ops = set()

        # Scan all n-grams (3→2→1) against hashmap
        # Larger n-grams checked first for more specific matches
        for n in [3, 2, 1]:
            if n > len(words):
                continue
            for i in range(len(words) - n + 1):
                ngram = " ".join(words[i:i + n])
                if ngram in self._index[n]:
                    matched_ops.add(self._index[n][ngram])

        # Default to CHAT if nothing matched
        if not matched_ops:
            return {
                "operations": ["CHAT"],
                "pipeline": [{"step": 1, "op": "CHAT", "depends_on": []}],
                "classified_by": "default",
            }

        # Handle special cases:
        # If COMPOSE is detected alongside ACTION (e.g., "draft and send"),
        # keep both — COMPOSE creates the content, ACTION sends it
        # If only COMPOSE without ACTION, it's just drafting (no send)

        # If SEARCH is a dependency but wasn't explicitly matched,
        # add it implicitly (CALCULATE/SUMMARIZE always need data)
        for op in list(matched_ops):
            if op in self.DEPENDENCIES:
                for dep in self.DEPENDENCIES[op]:
                    matched_ops.add(dep)

        operations = sorted(matched_ops, key=self._operation_priority)
        pipeline = self._build_pipeline(operations)

        return {
            "operations": operations,
            "pipeline": pipeline,
            "classified_by": "keywords",
        }

    # =========================================================================
    # PIPELINE BUILDER — Order operations by dependencies
    # =========================================================================

    def _build_pipeline(self, operations):
        """
        Build an ordered execution pipeline from operations.

        How it works:
            1. Find operations with NO dependencies → they go first (step 1)
            2. Find operations that depend on step 1 → they go in step 2
            3. Continue until all operations are placed

        Operations at the SAME step can run in parallel.

        Args:
            operations (list): List of operation strings, e.g. ["SEARCH", "CALCULATE"]

        Returns:
            list[dict] → ordered steps, each with:
                step       (int):  Step number (1-based)
                op         (str):  Operation name
                depends_on (list): Which operations must complete first

        Example:
            Input: ["SEARCH", "CALCULATE", "SUMMARIZE"]
            Output: [
                {"step": 1, "op": "SEARCH", "depends_on": []},
                {"step": 2, "op": "CALCULATE", "depends_on": ["SEARCH"]},
                {"step": 2, "op": "SUMMARIZE", "depends_on": ["SEARCH"]},
            ]
            Note: CALCULATE and SUMMARIZE are both step 2 → can run in parallel
        """
        pipeline = []
        placed = set()
        step = 1

        # Keep placing operations until all are in the pipeline
        max_iterations = len(operations) + 1  # Safety: prevent infinite loop
        iteration = 0

        while len(placed) < len(operations) and iteration < max_iterations:
            iteration += 1

            # Find operations whose dependencies are ALL already placed
            ready = []
            for op in operations:
                if op in placed:
                    continue

                deps = self.DEPENDENCIES.get(op, [])
                # Only consider deps that are actually in our operation list
                relevant_deps = [d for d in deps if d in operations]

                if all(d in placed for d in relevant_deps):
                    ready.append(op)

            if not ready:
                # No operations ready — shouldn't happen with valid deps
                # Place remaining as final step (fail-safe)
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

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _operation_priority(self, op):
        """
        Sort operations in logical execution order.

        Priority (lower = runs first):
            SEARCH → 0  (need data first)
            CALCULATE → 1 (count the data)
            SUMMARIZE → 2 (summarize the data)
            COMPOSE → 3 (write based on data)
            ACTION → 4 (execute the action)
            CHAT → 5 (no data needed)

        This is used for sorting the operations list, NOT for pipeline ordering.
        Pipeline ordering uses DEPENDENCIES dict (which is more precise).
        """
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
        """
        Get the recommended data source strategy for an operation.

        How it works:
            Each operation has a preferred data source:
            - SEARCH → local first (SQLite → ChromaDB → Composio API)
            - CALCULATE → uses SEARCH results (Python math, no external call)
            - ACTION → always Composio API (mutations must go to real service)
            - COMPOSE → LLM generates text (Groq → Gemini fallback)
            - SUMMARIZE → LLM summarizes data (Gemini → Groq fallback)
            - CHAT → LLM responds (Groq → Gemini fallback)

        Args:
            operation (str): One of the 6 operation types

        Returns:
            dict with:
                source    (str):  Primary data source name
                fallback  (list): Fallback sources in order
                cache     (bool): Whether to cache the result

        Example:
            strategy = classifier.get_data_source("SEARCH")
            # {"source": "sqlite", "fallback": ["chromadb", "composio"], "cache": True}

            strategy = classifier.get_data_source("ACTION")
            # {"source": "composio", "fallback": ["retry_once"], "cache": False}
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
                "cache": False,  # Actions invalidate cache, never cached
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


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_classifier_instance = None


def get_classifier():
    """
    Get the singleton OperationClassifier instance.

    Usage:
        from core.operation_classifier import get_classifier
        classifier = get_classifier()
        result = classifier.classify("show my emails")
    """
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = OperationClassifier()
    return _classifier_instance


# =============================================================================
# TEST BLOCK — Run with: python core/operation_classifier.py
# =============================================================================
if __name__ == "__main__":
    print("Testing OperationClassifier...")
    print("=" * 60)

    c = OperationClassifier()

    # Test queries covering all operation types
    test_queries = [
        # 1. Pure SEARCH
        ("show my unread emails", ["SEARCH"]),
        # 2. SEARCH + CALCULATE (count needs data first)
        ("how many emails from John?", ["CALCULATE", "SEARCH"]),
        # 3. Pure ACTION
        ("send email to boss", ["ACTION"]),
        # 4. SEARCH + SUMMARIZE (summarize needs data first)
        ("summarize slack #general", ["SEARCH", "SUMMARIZE"]),
        # 5. Pure CHAT (greeting)
        ("hello", ["CHAT"]),
        # 6. COMPOSE + ACTION (draft a reply — "reply" is an action word)
        ("draft a reply to the last email", ["COMPOSE", "ACTION"]),
        # 7. SEARCH + CALCULATE (total)
        ("what is the total amount from invoices?", ["CALCULATE", "SEARCH"]),
        # 8. Pure SEARCH with filter
        ("find emails about deployment since last week", ["SEARCH"]),
        # 9. ACTION (calendar)
        ("schedule a meeting tomorrow at 3pm", ["ACTION"]),
        # 10. SUMMARIZE + SEARCH
        ("give me a recap of today's messages", ["SEARCH", "SUMMARIZE"]),
        # 11. COMPOSE + ACTION (draft and send)
        ("write and send an email to john about the meeting", ["ACTION", "COMPOSE"]),
        # 12. CHAT (help)
        ("what can you do", ["CHAT"]),
        # 13. SEARCH with multiple signals
        ("show me the latest messages from #dev channel", ["SEARCH"]),
        # 14. CALCULATE with compare
        ("compare emails from john vs sarah", ["CALCULATE", "SEARCH"]),
    ]

    passed = 0
    failed = 0

    for query, expected_ops in test_queries:
        result = c.classify(query)
        actual_ops = result["operations"]

        # Check if all expected ops are present
        match = set(expected_ops) == set(actual_ops)
        status = "[OK]" if match else "[MISS]"

        if match:
            passed += 1
        else:
            failed += 1

        print(f"  {status} \"{query}\"")
        print(f"        Expected: {expected_ops}")
        print(f"        Got:      {actual_ops}")
        if result["pipeline"]:
            steps = ", ".join(
                f"step{p['step']}:{p['op']}" for p in result["pipeline"]
            )
            print(f"        Pipeline: {steps}")
        print()

    # Test dependency pipeline specifically
    print("-" * 60)
    print("Pipeline dependency tests:")
    print()

    # SEARCH+CALCULATE → SEARCH must be step 1, CALCULATE step 2
    r = c.classify("how many emails from john?")
    search_step = next(p for p in r["pipeline"] if p["op"] == "SEARCH")["step"]
    calc_step = next(p for p in r["pipeline"] if p["op"] == "CALCULATE")["step"]
    dep_ok = search_step < calc_step
    print(f"  {'[OK]' if dep_ok else '[FAIL]'} SEARCH(step {search_step}) before CALCULATE(step {calc_step})")

    # SEARCH+SUMMARIZE → SEARCH must be step 1, SUMMARIZE step 2
    r = c.classify("summarize my inbox")
    search_step = next(p for p in r["pipeline"] if p["op"] == "SEARCH")["step"]
    summ_step = next(p for p in r["pipeline"] if p["op"] == "SUMMARIZE")["step"]
    dep_ok2 = search_step < summ_step
    print(f"  {'[OK]' if dep_ok2 else '[FAIL]'} SEARCH(step {search_step}) before SUMMARIZE(step {summ_step})")

    # Data source test
    print()
    print("-" * 60)
    print("Data source strategies:")
    print()
    for op in ["SEARCH", "CALCULATE", "ACTION", "COMPOSE", "SUMMARIZE", "CHAT"]:
        ds = c.get_data_source(op)
        print(f"  {op:12} -> source: {ds['source']:10} fallback: {ds['fallback']}  cache: {ds['cache']}")

    print()
    print("=" * 60)
    print(f"[RESULTS] {passed} passed, {failed} missed out of {len(test_queries)} tests")

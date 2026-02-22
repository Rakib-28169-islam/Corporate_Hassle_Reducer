"""Base agent: shared foundation for Gmail, Slack, Outlook specialist agents.

Phase 7: Intent-driven search with deterministic execution.
LLM interprets (via QueryParser). Backend executes strictly.
"""

import re
import json
import logging

from core.llm_manager import get_brain_router
from core.operation_classifier import get_classifier
from core.query_parser import ParsedIntent
from tools.local_tools import get_local_tools
from services.sync_service import get_sync_service

logger = logging.getLogger(__name__)

# Default display fields per tool (for deterministic formatter)
# These match the canonical field names AFTER normalization
TOOL_DISPLAY_FIELDS = {
    "gmail": ["subject", "from", "date"],
    "slack": ["text", "user", "channel"],
    "outlook": ["subject", "from", "date", "start", "end"],
}


class BaseAgent:
    """Base class for all specialist agents. Provides shared operation handlers
    with intent-driven search routing. Subclasses override tool-specific methods."""

    def __init__(self, user_id="default", tool_name="general"):
        self.user_id = user_id
        self.tool_name = tool_name
        self.brain = get_brain_router()
        self.classifier = get_classifier()
        self.local_tools = get_local_tools()
        self.sync = get_sync_service()

    # --- Main entry point ---

    async def execute(self, query, operations=None, parsed_intent=None):
        """Run the classified operation pipeline and return merged results."""
        if operations is None:
            classification = self.classifier.classify(query)
            operations = classification["operations"]
            pipeline = classification["pipeline"]
        else:
            pipeline = self.classifier._build_pipeline(operations)

        intent = self._resolve_intent(parsed_intent)

        logger.info(f"[{self.tool_name}] Executing: {operations} | "
                    f"intent={intent.intent}/{intent.query_type} "
                    f"limit={intent.limit} | query: {query[:50]}")

        results = {}
        context = {"parsed_intent": intent}

        for step_info in pipeline:
            op = step_info["op"]
            try:
                results[op] = await self._dispatch_operation(op, query, context)
                if op == "SEARCH":
                    context["search_results"] = results["SEARCH"].get("data", [])
                    context["search_source"] = results["SEARCH"].get("source", "")
            except Exception as e:
                logger.error(f"[{self.tool_name}] {op} failed: {e}")
                results[op] = {"error": str(e)}

        # Format search results based on query_type
        await self._apply_formatting(query, operations, results, context, intent)

        return {
            "query": query,
            "operations": operations,
            "results": results,
            "final_answer": self._build_final_answer(query, operations, results),
            "parsed_intent": intent.to_dict(),
        }

    def _resolve_intent(self, parsed_intent_dict):
        """Reconstruct ParsedIntent from dict, or return default."""
        if parsed_intent_dict and isinstance(parsed_intent_dict, dict):
            try:
                return ParsedIntent(
                    intent=parsed_intent_dict.get("intent", "search"),
                    limit=parsed_intent_dict.get("limit", 20),
                    fields=parsed_intent_dict.get("fields", []),
                    sort=parsed_intent_dict.get("sort", "date_desc"),
                    filters=parsed_intent_dict.get("filters", {}),
                    query_type=parsed_intent_dict.get("query_type", "semantic"),
                    parsed_by=parsed_intent_dict.get("parsed_by", "dict"),
                )
            except Exception:
                pass
        return ParsedIntent.default()

    async def _apply_formatting(self, query, operations, results, context, intent):
        """Apply formatting based on query_type:
        - structured/hybrid: deterministic template (no LLM)
        - semantic: LLM summarize (for meaning-based presentation)
        """
        has_presentation = any(op in results for op in
                               ("SUMMARIZE", "CHAT", "COMPOSE"))
        if has_presentation or "SEARCH" not in results:
            return

        search_data = results["SEARCH"].get("data", [])
        if not search_data:
            return

        if intent.query_type in ("structured", "hybrid"):
            # Deterministic formatting — no LLM, zero hallucination
            results["FORMATTED"] = self._format_exact_results(search_data, intent)
        else:
            # Semantic: use LLM to summarize (with strict count constraint)
            context["search_results"] = search_data
            try:
                results["SUMMARIZE"] = await self._do_summarize(query, context)
                if "SUMMARIZE" not in operations:
                    operations.append("SUMMARIZE")
            except Exception as e:
                logger.error(f"[{self.tool_name}] Auto-summarize failed: {e}")

    async def _dispatch_operation(self, op, query, context):
        """Route a single operation to its handler."""
        handlers = {
            "SEARCH": self._do_search,
            "CALCULATE": self._do_calculate,
            "ACTION": self._do_action,
            "COMPOSE": self._do_compose,
            "SUMMARIZE": self._do_summarize,
            "CHAT": self._do_chat,
        }
        handler = handlers.get(op)
        if handler:
            return await handler(query, context)
        return {"error": f"Unknown operation: {op}"}

    # --- SEARCH (intent-driven routing) ---

    async def _do_search(self, query, context):
        """Search using intent-driven routing:
        - structured → SQLite only (deterministic SQL)
        - hybrid → ChromaDB IDs → SQLite filter+limit
        - semantic → full chain (LLM SQL → SQLite → ChromaDB)
        """
        intent = context.get("parsed_intent", ParsedIntent.default())

        stale = await self.local_tools.is_stale(
            self.user_id, self.tool_name, max_age_minutes=30
        )

        # Route to correct search method based on query_type
        local_result = await self._search_by_type(query, intent)

        local_data = local_result.get("results", [])
        source = local_result.get("source", "")

        # Post-processing guard: ALWAYS enforce limit
        local_data = local_data[:intent.limit]

        if local_data and not stale:
            logger.info(f"[{self.tool_name}] SEARCH: {len(local_data)} results "
                        f"from {source} (type={intent.query_type})")
            return self._make_search_result(local_data, source)

        logger.info(f"[{self.tool_name}] SEARCH: local "
                    f"{'stale' if stale else 'empty'}, calling Composio API...")

        api_result = await self._try_composio_search(query)
        if api_result:
            # Post-processing guard on API results too
            api_data = api_result.get("data", [])[:intent.limit]
            return self._make_search_result(api_data, "composio")

        if local_data:
            logger.info(f"[{self.tool_name}] API failed, returning stale data")
            return self._make_search_result(local_data, f"{source}_stale")

        return {"data": [], "source": "empty", "count": 0}

    async def _search_by_type(self, query, intent):
        """Route search to correct method based on query_type."""
        if intent.query_type == "structured":
            return await self.local_tools.search_structured(
                user_id=self.user_id, tool=self.tool_name, intent=intent,
            )
        elif intent.query_type == "hybrid":
            return await self.local_tools.search_hybrid(
                user_id=self.user_id, tool=self.tool_name,
                query=query, intent=intent,
            )
        else:
            # Semantic: use existing full chain
            return await self.local_tools.search_local(
                user_id=self.user_id, tool=self.tool_name,
                query=query, limit=intent.limit,
            )

    async def _try_composio_search(self, query):
        """Try Composio API and cache results. Returns search result or None."""
        try:
            api_results = self._composio_search(query)
            if not api_results:
                return None

            items = self._normalize_api_results(api_results)
            if not items:
                return None

            # Normalize Composio field names to canonical names
            from services.data_fetch_service import get_data_fetch_service
            items = get_data_fetch_service().normalize_tool_fields(
                items, self.tool_name
            )

            await self.local_tools.cache_results(
                user_id=self.user_id, tool=self.tool_name,
                data_type=self._get_data_type(), items=items,
            )
            return self._make_search_result(items, "composio")
        except Exception as e:
            logger.error(f"[{self.tool_name}] Composio API failed: {e}")
            return None

    def _make_search_result(self, data, source):
        """Build a standard search result dict."""
        return {"data": data, "source": source, "count": len(data)}

    # --- Deterministic formatter (no LLM) ---

    def _format_exact_results(self, data, intent):
        """Format search results deterministically for structured/hybrid queries.
        Uses only actual data fields — zero hallucination possible.
        """
        if not data:
            return {"formatted": "No results found.", "method": "deterministic"}

        # For count queries, just return the count
        if intent.intent == "count":
            if data and isinstance(data[0], dict) and "count" in data[0]:
                count_val = data[0]["count"]
            else:
                count_val = len(data)
            return {"formatted": f"Count: {count_val}",
                    "method": "deterministic"}

        # Determine display fields
        display_fields = (intent.fields if intent.fields
                          else self._auto_detect_fields(data))

        lines = []
        for i, item in enumerate(data, 1):
            if not isinstance(item, dict):
                lines.append(f"{i}. {str(item)[:200]}")
                continue

            parts = self._format_item_fields(item, display_fields)
            if parts:
                lines.append(f"{i}. {' | '.join(parts)}")
            else:
                # Fallback: show first 3 key-value pairs
                fallback = [f"{k}: {str(v)[:60]}"
                            for k, v in list(item.items())[:3]]
                lines.append(f"{i}. {' | '.join(fallback)}")

        header = f"Found {len(data)} result(s):"
        formatted_text = "\n".join(lines)

        return {
            "formatted": f"{header}\n{formatted_text}",
            "method": "deterministic",
            "fields_shown": display_fields,
            "count": len(data),
        }

    def _format_item_fields(self, item, display_fields):
        """Format a single item using display fields."""
        parts = []
        for field in display_fields:
            value = item.get(field)
            if value is None:
                continue
            label = field.replace("_", " ").title()
            str_val = str(value)
            if len(str_val) > 100:
                str_val = str_val[:97] + "..."
            parts.append(f"{label}: {str_val}")
        return parts

    def _auto_detect_fields(self, data):
        """Auto-detect best display fields based on tool and available data."""
        default_fields = TOOL_DISPLAY_FIELDS.get(self.tool_name, [])
        if not data or not isinstance(data[0], dict):
            return default_fields

        sample = data[0]
        detected = [f for f in default_fields if f in sample]
        return detected if detected else list(sample.keys())[:4]

    # --- CALCULATE (multi-strategy) ---

    async def _do_calculate(self, query, context):
        """Calculate from search results using the best available strategy."""
        search_data = context.get("search_results", [])
        search_source = context.get("search_source", "")
        query_lower = query.lower()

        if search_source in ("llm_sql", "structured_sql") \
                and self._is_aggregation_result(search_data):
            return self._calc_aggregation(search_data)

        if any(w in query_lower for w in ["how many", "count", "number of"]):
            return self._calc_simple("python", len(search_data))

        if any(w in query_lower for w in ["total", "sum", "add up"]):
            return self._calc_sum(search_data)

        if any(w in query_lower for w in
               ["most", "least", "top", "highest", "lowest"]):
            return self._calc_simple("python", len(search_data),
                                     f"Found {len(search_data)} items to compare")

        if search_data:
            return await self._calc_with_llm(query, search_data)

        return self._calc_simple("fallback", len(search_data),
                                 f"Found {len(search_data)} items")

    def _calc_aggregation(self, data):
        parts = []
        for row in data:
            vals = list(row.values())
            parts.append(": ".join(str(v) for v in vals))
        return {"answer": ", ".join(parts), "value": data, "method": "python"}

    def _calc_simple(self, method, value, answer=None):
        return {"answer": answer or str(value), "value": value, "method": method}

    def _calc_sum(self, search_data):
        currency_hits = self._extract_currency(search_data)
        if currency_hits:
            total = sum(h["amount"] for h in currency_hits)
            return self._calc_simple("python", total)

        numbers = self._extract_numbers(search_data)
        if numbers:
            return self._calc_simple("python", sum(numbers))

        return self._calc_simple("python", 0, "No numeric values found")

    async def _calc_with_llm(self, query, search_data):
        try:
            data_summary = json.dumps(search_data[:10], default=str)
            content = self.brain.invoke_with_fallback(
                f"Based on this data, answer the calculation question.\n"
                f"Question: {query}\nData: {data_summary}\n"
                f"Give a short, direct numerical answer.",
                task_type="fast",
            )
            return {"answer": content, "value": content, "method": "llm"}
        except Exception as e:
            logger.error(f"[{self.tool_name}] LLM calculate failed: {e}")
            return self._calc_simple("fallback", len(search_data),
                                     f"Found {len(search_data)} items")

    # --- ACTION (override in subclass) ---

    async def _do_action(self, query, context):
        return {
            "action": "unknown", "status": "not_implemented",
            "message": f"Action not implemented for {self.tool_name}. "
                       f"Override _do_action() in the specialist agent.",
        }

    # --- COMPOSE ---

    async def _do_compose(self, query, context):
        search_data = context.get("search_results", [])
        context_text = ""
        if search_data:
            context_text = (f"\n\nContext (original content to reference):\n"
                            f"{json.dumps(search_data[:3], default=str)}")

        prompt = (f"You are a professional assistant. {query}"
                  f"{context_text}\n\nWrite the requested content. "
                  f"Be professional and concise.")
        try:
            content = self.brain.invoke_with_fallback(prompt, task_type="general")
            return {"content": content, "model": "llm"}
        except Exception as e:
            logger.error(f"[{self.tool_name}] Compose failed (all LLMs): {e}")
            return {"content": None, "model": "none", "error": "All LLMs failed"}

    # --- SUMMARIZE ---

    async def _do_summarize(self, query, context):
        """Summarize search results using LLM with strict count constraint."""
        search_data = context.get("search_results", [])

        if not search_data:
            return {"summary": "No data found to summarize.",
                    "model": "none", "items_summarized": 0}

        count = len(search_data)
        data_text = json.dumps(search_data[:20], default=str)
        prompt = (f"Summarize the following EXACTLY {count} items based on "
                  f"the user's request.\n"
                  f"User asked: {query}\n\n"
                  f"Data ({count} items):\n{data_text}\n\n"
                  f"Rules:\n"
                  f"- ONLY use information from the provided {count} items\n"
                  f"- Do NOT invent additional items or data\n"
                  f"- Be concise (bullet points preferred)\n"
                  f"- Highlight key items, dates, and action items\n"
                  f"- If data is insufficient, say so")

        try:
            content = self.brain.invoke_with_fallback(prompt, task_type="reading")
            return {"summary": content, "model": "llm",
                    "items_summarized": count}
        except Exception as e:
            logger.error(f"[{self.tool_name}] Summarize failed (all LLMs): {e}")
            return {"summary": "All LLMs failed for summarization.",
                    "model": "none", "items_summarized": 0}

    # --- CHAT ---

    async def _do_chat(self, query, context):
        try:
            content = self.brain.invoke_with_fallback(
                f"You are a helpful corporate assistant. "
                f"Respond briefly and helpfully.\n\nUser: {query}",
                task_type="fast",
            )
            return {"response": content, "model": "llm"}
        except Exception as e:
            logger.error(f"[{self.tool_name}] Chat failed (all LLMs): {e}")
            return {"response": "Hello! How can I help you?", "model": "none"}

    # --- Subclass hooks ---

    def _composio_search(self, query):
        raise NotImplementedError(
            f"{self.__class__.__name__} must override _composio_search()")

    def _get_data_type(self):
        return "data"

    # --- Helpers ---

    def _normalize_api_results(self, api_response):
        """Normalize Composio API response into a list of dicts.

        Handles ToolExecuteResponse objects (which have .data attribute),
        plain lists, dicts with data/results/messages keys, and raw strings.
        """
        if api_response is None:
            return []

        # Handle Composio ToolExecuteResponse objects
        if hasattr(api_response, "data"):
            api_response = api_response.data
        if hasattr(api_response, "response_data"):
            api_response = api_response.response_data

        if isinstance(api_response, list):
            return api_response
        if isinstance(api_response, dict):
            for key in ("data", "results", "messages", "items", "emails"):
                if key in api_response:
                    val = api_response[key]
                    if isinstance(val, list):
                        return val
                    if isinstance(val, dict):
                        return [val]
            # Single result dict (has identifiable fields)
            if any(k in api_response for k in
                   ("id", "subject", "text", "messageId", "ts")):
                return [api_response]
            return [api_response]
        if isinstance(api_response, str):
            return [{"content": api_response}]
        return []

    def _extract_numbers(self, data):
        numbers = []
        for item in data:
            if not isinstance(item, dict):
                continue
            for value in item.values():
                if isinstance(value, (int, float)):
                    numbers.append(float(value))
                elif isinstance(value, str):
                    for n in re.findall(r'[\d,]+\.?\d*', value):
                        try:
                            numbers.append(float(n.replace(',', '')))
                        except ValueError:
                            pass
        return numbers

    def _is_aggregation_result(self, data):
        if not data or not isinstance(data[0], dict):
            return False
        agg_keys = {"count", "cnt", "total", "avg", "sum", "min", "max"}
        return bool(agg_keys & {k.lower() for k in data[0].keys()})

    def _extract_currency(self, data):
        PATTERN = (
            r'(?:[\$\€\£\¥\৳]\s?)'
            r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)'
            r'|'
            r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)'
            r'\s*(?:USD|EUR|GBP|BDT|TK|INR|JPY)'
        )
        results = []
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ("subject", "body", "text", "snippet"):
                text = item.get(key, "")
                if not isinstance(text, str):
                    continue
                for groups in re.findall(PATTERN, text, re.IGNORECASE):
                    val_str = groups[0] or groups[1]
                    if val_str:
                        try:
                            results.append({
                                "amount": float(val_str.replace(",", "")),
                                "source": item.get("subject",
                                                   item.get("text", ""))[:40],
                            })
                        except ValueError:
                            pass
        return results

    def _build_final_answer(self, query, operations, results):
        """Combine per-operation results into one user-facing answer string."""
        parts = []

        # Deterministic formatting takes priority
        if "FORMATTED" in results:
            formatted = results["FORMATTED"].get("formatted", "")
            if formatted:
                parts.append(formatted)
        elif "SEARCH" in results:
            s = results["SEARCH"]
            count = s.get("count", 0)
            parts.append(
                f"Found {count} results (from {s.get('source', 'unknown')})."
                if count > 0 else "No results found."
            )

        if "CALCULATE" in results:
            answer = results["CALCULATE"].get("answer", "")
            if answer:
                parts.append(f"Calculation: {answer}")

        if "COMPOSE" in results:
            content = results["COMPOSE"].get("content", "")
            if content:
                parts.append(f"Draft:\n{content}")

        if "SUMMARIZE" in results:
            summary = results["SUMMARIZE"].get("summary", "")
            if summary:
                parts.append(summary)

        if "ACTION" in results:
            a = results["ACTION"]
            parts.append(f"Action: {a.get('message', '')} "
                         f"(status: {a.get('status', '')})")

        if "CHAT" in results:
            response = results["CHAT"].get("response", "")
            if response:
                parts.append(response)

        return "\n\n".join(parts) if parts else "I processed your request."

    async def _notify_action_complete(self, action_type):
        await self.sync.on_action_complete(
            self.user_id, self.tool_name, action_type
        )

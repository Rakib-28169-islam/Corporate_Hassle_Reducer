"""LocalToolManager — unified local search with intent-driven routing.

Three search modes:
- search_structured(): SQLite only — deterministic SQL from intent (exact queries)
- search_hybrid():     ChromaDB IDs → SQLite filter+limit (precision + meaning)
- search_local():      SQLite keyword → ChromaDB semantic (no LLM SQL)
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from database import get_database, get_vector_store
from core.sql_generator import TOOL_SCHEMA

logger = logging.getLogger(__name__)

# If SQLite returns >= this many results, skip ChromaDB entirely.
SMART_THRESHOLD = 3


class LocalToolManager:
    """Unified interface for local data access (SQLite + ChromaDB).
    Agents never import database modules directly; they go through this.
    """

    def __init__(self):
        self.db = get_database()
        self.vs = get_vector_store()
        logger.info("LocalToolManager initialized")

    async def search_local(self, user_id, tool=None, query=None,
                           data_type=None, limit=20):
        """Search local DBs: SQLite keyword first, then ChromaDB semantic.
        Uses smart threshold to skip ChromaDB when SQLite has enough results.
        """
        sqlite_results = []
        chromadb_results = []

        try:
            sqlite_results = await self.db.search_memory(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                query=query,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"SQLite search failed: {e}")

        sqlite_count = len(sqlite_results)

        if sqlite_count >= SMART_THRESHOLD:
            logger.debug(f"Threshold met: SQLite returned {sqlite_count} results "
                         f"(>= {SMART_THRESHOLD}), skipping ChromaDB")
            return {
                "results": [r["content"] for r in sqlite_results],
                "source": "sqlite",
                "sqlite_count": sqlite_count,
                "chromadb_count": 0,
                "threshold_met": True,
            }

        if query:
            try:
                chromadb_results = self.vs.search(
                    query=query,
                    user_id=user_id,
                    tool=tool,
                    data_type=data_type,
                    n_results=limit,
                )
            except Exception as e:
                logger.error(f"ChromaDB search failed: {e}")

        return self._merge_results(sqlite_results, chromadb_results, limit)

    def _merge_results(self, sqlite_results, chroma_results, limit):
        """Merge and deduplicate SQLite + ChromaDB results by external_id.
        SQLite results take priority (full JSON content); ChromaDB fills gaps.
        """
        seen_ids = set()
        merged = []

        for r in sqlite_results:
            ext_id = r.get("external_id")
            if ext_id:
                seen_ids.add(ext_id)
            merged.append(r["content"])

        chromadb_added = 0
        for r in chroma_results:
            ext_id = r.get("external_id", "")
            if ext_id and ext_id in seen_ids:
                continue
            if len(merged) >= limit:
                break
            merged.append({
                "text": r.get("text", ""),
                "tool": r.get("tool", ""),
                "data_type": r.get("data_type", ""),
                "external_id": ext_id,
                "source": "chromadb",
                "score": r.get("score", 0.0),
            })
            seen_ids.add(ext_id)
            chromadb_added += 1

        sqlite_count = len(sqlite_results)
        if not merged:
            source = "empty"
        elif sqlite_count > 0 and chromadb_added > 0:
            source = "hybrid"
        elif sqlite_count > 0:
            source = "sqlite"
        elif chromadb_added > 0:
            source = "chromadb"
        else:
            source = "empty"

        return {
            "results": merged,
            "source": source,
            "sqlite_count": sqlite_count,
            "chromadb_count": chromadb_added,
            "threshold_met": False,
        }

    # --- Intent-driven search (Phase 7) ---

    async def search_structured(self, user_id, tool, intent):
        """SQLite only — deterministic SQL from validated intent. No LLM.

        Used for structured queries: exact filters, dates, counts, limits.
        Falls back to search_local() on any failure.
        """
        try:
            sql = self._build_intent_sql(user_id, tool, intent)
            rows = await self.db.execute_sql(sql)

            results = self._parse_content_rows(rows)

            # Post-processing guard: enforce limit
            limit = getattr(intent, "limit", 20)
            results = results[:limit]

            logger.info(f"search_structured: {len(results)} results | "
                        f"SQL: {sql[:120]}")
            return {"results": results, "source": "structured_sql",
                    "count": len(results)}
        except Exception as e:
            logger.error(f"search_structured failed: {e}, falling back")
            keyword = ""
            if hasattr(intent, "filters"):
                keyword = intent.filters.get("keyword", "")
            return await self.search_local(
                user_id=user_id, tool=tool, query=keyword,
                limit=getattr(intent, "limit", 20),
            )

    async def search_hybrid(self, user_id, tool, query, intent):
        """ChromaDB finds candidates by meaning → SQLite filters + limits.

        Used for hybrid queries: "last 5 emails about marketing".
        Over-fetches from ChromaDB with min(limit*5, 100) buffer.
        """
        try:
            keyword = intent.filters.get("keyword", query)
            limit = getattr(intent, "limit", 20)
            overfetch = min(limit * 5, 100)

            # Step 1: ChromaDB semantic search for candidate IDs
            chroma_results = self.vs.search(
                query=keyword, user_id=user_id, tool=tool,
                n_results=overfetch,
            )

            if not chroma_results:
                logger.info("search_hybrid: no ChromaDB candidates, "
                            "falling back to structured")
                return await self.search_structured(user_id, tool, intent)

            candidate_ids = [r.get("external_id", "") for r in chroma_results
                             if r.get("external_id")]

            if not candidate_ids:
                return await self.search_structured(user_id, tool, intent)

            # Step 2: SQLite filter with candidate IDs + intent filters
            sql = self._build_hybrid_sql(user_id, tool, intent, candidate_ids)
            rows = await self.db.execute_sql(sql)
            results = self._parse_content_rows(rows)

            # Post-processing guard
            results = results[:limit]

            logger.info(f"search_hybrid: {len(chroma_results)} candidates → "
                        f"{len(results)} filtered results")
            return {"results": results, "source": "hybrid",
                    "count": len(results)}
        except Exception as e:
            logger.error(f"search_hybrid failed: {e}, falling back")
            return await self.search_local(
                user_id=user_id, tool=tool, query=query,
                limit=getattr(intent, "limit", 20),
            )

    def _build_intent_sql(self, user_id, tool, intent):
        """Build deterministic SQL from ParsedIntent. No LLM involved."""
        conditions = [f"user_id = '{user_id}'"]

        if tool:
            conditions.append(f"tool = '{tool}'")

        filters = getattr(intent, "filters", {})

        for key, value in filters.items():
            condition = self._filter_to_sql(key, value, tool)
            if condition:
                conditions.append(condition)

        # SELECT
        intent_type = getattr(intent, "intent", "search")
        if intent_type == "count":
            select = "SELECT COUNT(*) as count"
        else:
            select = "SELECT content"

        # ORDER BY
        sort = getattr(intent, "sort", "date_desc")
        order = "ORDER BY cached_at ASC" if sort == "date_asc" \
            else "ORDER BY cached_at DESC"

        # LIMIT
        limit = min(getattr(intent, "limit", 20), 100)

        where = " AND ".join(conditions)
        return f"{select} FROM memory WHERE {where} {order} LIMIT {limit}"

    def _build_hybrid_sql(self, user_id, tool, intent, candidate_ids):
        """Build SQL with candidate IDs from ChromaDB + intent filters."""
        conditions = [f"user_id = '{user_id}'"]

        if tool:
            conditions.append(f"tool = '{tool}'")

        # Filter to candidate IDs from ChromaDB
        escaped_ids = ", ".join(
            f"'{cid.replace(chr(39), chr(39)+chr(39))}'"
            for cid in candidate_ids
        )
        conditions.append(f"external_id IN ({escaped_ids})")

        # Apply exact filters (skip keyword — ChromaDB already handled meaning)
        filters = getattr(intent, "filters", {})
        for key, value in filters.items():
            if key == "keyword":
                continue
            condition = self._filter_to_sql(key, value, tool)
            if condition:
                conditions.append(condition)

        sort = getattr(intent, "sort", "date_desc")
        order = "ORDER BY cached_at ASC" if sort == "date_asc" \
            else "ORDER BY cached_at DESC"
        limit = min(getattr(intent, "limit", 20), 100)

        where = " AND ".join(conditions)
        return f"SELECT content FROM memory WHERE {where} {order} LIMIT {limit}"

    def _filter_to_sql(self, key, value, tool=None):
        """Convert a single intent filter to a SQL condition."""
        if value is None:
            return None

        escaped = str(value).replace("'", "''")

        if key == "keyword":
            return f"content LIKE '%{escaped}%'"
        elif key == "date_from":
            return f"json_extract(content, '$.date') >= '{escaped}'"
        elif key == "date_to":
            return f"json_extract(content, '$.date') <= '{escaped}'"
        elif key in ("unread", "has_attachment", "is_read"):
            bool_val = 1 if value else 0
            return f"json_extract(content, '$.{key}') = {bool_val}"
        elif key in ("from", "to", "subject", "channel", "user"):
            return f"json_extract(content, '$.{key}') LIKE '%{escaped}%'"
        elif key == "labels":
            return f"json_extract(content, '$.labels') LIKE '%{escaped}%'"

        return None

    def _parse_content_rows(self, rows):
        """Parse content JSON from SQL result rows."""
        results = []
        for row in rows:
            if "content" in row:
                content = row.get("content", "{}")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append(content)
            else:
                results.append(dict(row))
        return results

    async def cache_results(self, user_id, tool, data_type, items,
                            id_field="id"):
        """Cache multiple items in both SQLite and ChromaDB.
        Returns dict with sqlite_count, chromadb_count, errors.
        """
        errors = []
        sqlite_count = 0
        chromadb_count = 0

        try:
            await self.db.cache_batch(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                items=items,
                id_field=id_field,
            )
            sqlite_count = len(items)
        except Exception as e:
            errors.append(f"SQLite cache_batch failed: {e}")
            logger.error(f"SQLite cache_batch failed: {e}")

        try:
            self.vs.add_documents(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                items=items,
                id_field=id_field,
            )
            chromadb_count = len(items)
        except Exception as e:
            errors.append(f"ChromaDB add_documents failed: {e}")
            logger.error(f"ChromaDB add_documents failed: {e}")

        logger.info(f"Cached {len(items)} items for {user_id}/{tool}/{data_type} "
                     f"(SQLite: {sqlite_count}, ChromaDB: {chromadb_count})")

        return {
            "sqlite_count": sqlite_count,
            "chromadb_count": chromadb_count,
            "errors": errors,
        }

    async def cache_single(self, user_id, tool, data_type, content,
                           external_id):
        """Cache one item in both SQLite and ChromaDB.
        Returns dict with sqlite_ok, chromadb_ok, errors.
        """
        errors = []
        sqlite_ok = False
        chromadb_ok = False

        try:
            await self.db.cache_data(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                content=content,
                external_id=external_id,
            )
            sqlite_ok = True
        except Exception as e:
            errors.append(f"SQLite cache_data failed: {e}")
            logger.error(f"SQLite cache_data failed: {e}")

        try:
            self.vs.add_single(
                user_id=user_id,
                tool=tool,
                data_type=data_type,
                content=content,
                external_id=external_id,
            )
            chromadb_ok = True
        except Exception as e:
            errors.append(f"ChromaDB add_single failed: {e}")
            logger.error(f"ChromaDB add_single failed: {e}")

        return {
            "sqlite_ok": sqlite_ok,
            "chromadb_ok": chromadb_ok,
            "errors": errors,
        }

    async def invalidate(self, user_id, tool=None):
        """Clear cached data for a user (optionally scoped to one tool).
        Call after mutations (send, delete, move) to force fresh API fetch.
        """
        errors = []
        sqlite_ok = False
        chromadb_ok = False

        try:
            await self.db.invalidate_cache(user_id=user_id, tool=tool)
            sqlite_ok = True
        except Exception as e:
            errors.append(f"SQLite invalidation failed: {e}")
            logger.error(f"SQLite invalidation failed: {e}")

        try:
            self.vs.delete_by_tool(user_id=user_id, tool=tool)
            chromadb_ok = True
        except Exception as e:
            errors.append(f"ChromaDB invalidation failed: {e}")
            logger.error(f"ChromaDB invalidation failed: {e}")

        logger.info(f"Cache invalidated for {user_id}"
                     f"{f'/{tool}' if tool else '/ALL'}")

        return {
            "sqlite_ok": sqlite_ok,
            "chromadb_ok": chromadb_ok,
            "errors": errors,
        }

    async def is_stale(self, user_id, tool, max_age_minutes=30):
        """Check if cached data is older than max_age_minutes.
        Returns True if stale or missing, False if fresh.
        """
        try:
            results = await self.db.search_memory(
                user_id=user_id,
                tool=tool,
                limit=1,
            )

            if not results:
                return True

            cached_at_str = results[0].get("cached_at", "")
            if not cached_at_str:
                return True

            cached_at = datetime.fromisoformat(cached_at_str)
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            age = now - cached_at
            max_age = timedelta(minutes=max_age_minutes)

            is_old = age > max_age
            if is_old:
                logger.debug(f"Cache stale for {user_id}/{tool}: "
                             f"age={age.total_seconds():.0f}s > max={max_age.total_seconds():.0f}s")
            return is_old

        except Exception as e:
            logger.error(f"Staleness check failed for {user_id}/{tool}: {e}")
            return True

    async def get_local_count(self, user_id, tool=None):
        """Count cached items for a user (optionally scoped to one tool).
        Returns dict with sqlite_count, chromadb_count, has_data.
        """
        sqlite_count = 0
        chromadb_count = 0

        try:
            sqlite_count = await self.db.get_memory_count(
                user_id=user_id, tool=tool
            )
        except Exception as e:
            logger.error(f"SQLite count failed: {e}")

        try:
            chromadb_count = self.vs.count()
        except Exception as e:
            logger.error(f"ChromaDB count failed: {e}")

        return {
            "sqlite_count": sqlite_count,
            "chromadb_count": chromadb_count,
            "has_data": (sqlite_count > 0 or chromadb_count > 0),
        }


_local_tools_instance = None


def get_local_tools():
    """Get the singleton LocalToolManager instance."""
    global _local_tools_instance
    if _local_tools_instance is None:
        _local_tools_instance = LocalToolManager()
    return _local_tools_instance

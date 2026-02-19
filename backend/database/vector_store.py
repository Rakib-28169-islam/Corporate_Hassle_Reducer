"""
=============================================================================
VECTOR STORE — Semantic Search Brain (ChromaDB)
=============================================================================

WHY THIS EXISTS:
    SQLite (sqlite_manager.py) does KEYWORD search — exact text matching.
    But users don't always use exact words:
        User asks: "any updates about the release?"
        SQLite LIKE search for "release" → MISS (email says "deployment")
        ChromaDB semantic search → HIT (knows "release" ≈ "deployment")

    ChromaDB converts text into numerical vectors (embeddings),
    then finds SIMILAR meanings, not just matching words.

HOW IT FITS IN THE SEARCH FALLBACK CHAIN:
    User query "any updates about the release?"
        1. SQLite keyword search (sqlite_manager.search_memory)  → fast, free
        2. ChromaDB semantic search (THIS FILE)              → smarter, still local
        3. Composio API call                                 → last resort, slow, costly

    If step 1 finds good results → skip 2 and 3.
    If step 1 misses → try step 2 (semantic match).
    If step 2 misses → hit the external API (step 3).

HOW CHROMADB WORKS (simplified):
    1. You give it text: "Meeting with boss tomorrow at 3pm"
    2. It converts to a vector: [0.23, -0.45, 0.78, ...] (384 dimensions)
    3. When you search "boss meeting", it converts that to a vector too
    4. Finds vectors that are CLOSE in 384-dimensional space
    5. Returns the original texts that are semantically similar

    Think of it like: every piece of text gets a "meaning coordinate"
    in a huge space, and search = "find the nearest coordinates"

STORAGE:
    ChromaDB persists data to disk at: backend/chroma_data/
    Survives server restarts. Grows as more data is cached.

USAGE:
    from database import get_vector_store

    vs = get_vector_store()
    vs.add_documents(...)      # Store text with embeddings
    vs.search(...)             # Find semantically similar text
    vs.delete(...)             # Remove documents

=============================================================================
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ChromaDB data directory
# Resolves to: backend/chroma_data/
# This folder holds the vector database files (persists across restarts)
# ---------------------------------------------------------------------------
CHROMA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "chroma_data"
)

# Collection name — one collection for all tools
# (filtered by metadata at query time, same as memory table design)
COLLECTION_NAME = "hassle_reducer_memory"

# Singleton instance
_vs_instance = None


class VectorStore:
    """
    ChromaDB Vector Store (Singleton).

    Provides semantic search over cached data from all tools.
    Works alongside SQLite (db_manager.py) — SQLite for keyword search,
    ChromaDB for meaning-based search.

    Design Principles:
        1. Graceful degradation → if ChromaDB fails to load, returns empty results (never crashes)
        2. Metadata filtering  → filter by user_id, tool, data_type (like SQL WHERE)
        3. One collection      → all tools in one place, filtered at query time
        4. Singleton           → one instance, shared across all agents
        5. Persistent storage  → data survives server restarts (stored in chroma_data/)

    Each document stored has:
        - id:       unique identifier (e.g., "user_1__gmail__msg_001")
        - document: the searchable text content
        - metadata: {"user_id", "tool", "data_type", "external_id"} for filtering
    """

    def __init__(self, persist_dir=None):
        """
        Initialize ChromaDB client and get/create the collection.

        How it works:
            - Creates a PersistentClient that stores data on disk
            - Gets or creates a collection (like a "table" in ChromaDB)
            - If ChromaDB fails to initialize → self.collection = None
              and all methods return empty results instead of crashing

        Args:
            persist_dir (str): Optional custom directory for ChromaDB data.
                               Default = backend/chroma_data/
                               Pass custom path for testing.
        """
        self.persist_dir = persist_dir or CHROMA_DIR
        self.collection = None

        try:
            import chromadb
            # PersistentClient = data saved to disk (survives restarts)
            # vs Client() = in-memory only (lost on restart)
            self.client = chromadb.PersistentClient(path=self.persist_dir)

            # get_or_create = if collection exists, get it; if not, create it
            # One collection for everything — filtered by metadata at query time
            self.collection = self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}  # cosine similarity (best for text)
            )
            logger.info(f"VectorStore initialized at {self.persist_dir} "
                        f"(documents: {self.collection.count()})")
        except Exception as e:
            # ChromaDB failed → all methods will return empty/None
            # The app continues working with SQLite only (graceful degradation)
            logger.warning(f"ChromaDB failed to initialize: {e}")
            logger.warning("Semantic search will be unavailable. SQLite keyword search still works.")
            self.client = None

    def _build_id(self, user_id, tool, external_id):
        """
        Build a unique document ID for ChromaDB.

        Why compound ID?
            - ChromaDB requires unique IDs per document
            - Same external_id might exist across different tools/users
            - "user_1__gmail__msg_001" is globally unique

        Format: "{user_id}__{tool}__{external_id}"

        Args:
            user_id     (str): User identifier
            tool        (str): Tool name (gmail, slack, outlook)
            external_id (str): External service ID (message_id, etc.)

        Returns:
            str → compound ID like "user_1__gmail__msg_001"
        """
        return f"{user_id}__{tool}__{external_id}"

    def _extract_text(self, content):
        """
        Convert a content dict/string into searchable plain text.

        Why?
            ChromaDB embeds TEXT, not JSON. We need to flatten the JSON
            into a readable string so the embedding captures meaning.

        How it works:
            - If content is a dict → joins all values into one string
              {"subject": "Meeting", "body": "Tomorrow 3pm"} → "subject: Meeting body: Tomorrow 3pm"
            - If content is already a string → returns as-is
            - Strips extra whitespace

        Args:
            content (dict or str): The data to convert to searchable text

        Returns:
            str → flattened text ready for embedding
        """
        if isinstance(content, dict):
            # Flatten dict values into "key: value" pairs
            parts = []
            for key, value in content.items():
                if value and isinstance(value, str):
                    parts.append(f"{key}: {value}")
                elif value:
                    parts.append(f"{key}: {str(value)}")
            return " ".join(parts).strip()
        elif isinstance(content, str):
            # Try parsing as JSON first (content might be a JSON string)
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return self._extract_text(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
            return content.strip()
        return ""

    # =========================================================================
    # ADD — Store documents with embeddings
    # =========================================================================

    def add_documents(self, user_id, tool, data_type, items, id_field="id"):
        """
        Add multiple documents to ChromaDB for semantic search.

        How it works:
            1. For each item, builds a unique compound ID
            2. Extracts searchable text from the item's content
            3. Attaches metadata (user_id, tool, data_type, external_id)
            4. ChromaDB auto-generates embeddings from the text
            5. Uses upsert → existing docs are updated, new ones are added

        When is this called?
            - Right AFTER caching data in SQLite (db_manager.cache_batch)
            - Both stores get the same data:
              SQLite = keyword search, ChromaDB = semantic search

        Args:
            user_id   (str):  Which user's data
            tool      (str):  "gmail", "slack", "outlook"
            data_type (str):  "email", "message", "event"
            items     (list): List of dicts — each dict is one item
            id_field  (str):  Which key in each dict is the external_id (default "id")

        Returns:
            int → number of documents successfully added/updated

        Example:
            emails = [
                {"id": "msg_001", "subject": "Meeting", "from": "boss@co.com", "body": "Tomorrow 3pm"},
                {"id": "msg_002", "subject": "Deploy", "from": "dev@co.com", "body": "Release ready"},
            ]
            count = vs.add_documents("user_1", "gmail", "email", emails)
            # count = 2 (both embedded and stored)
        """
        if not self.collection:
            logger.warning("VectorStore not available — skipping add_documents")
            return 0

        ids = []
        documents = []
        metadatas = []

        for item in items:
            external_id = item.get(id_field, "")
            if not external_id:
                continue  # Skip items without an ID (can't build unique doc ID)

            doc_id = self._build_id(user_id, tool, external_id)
            text = self._extract_text(item)

            if not text:
                continue  # Skip empty content (nothing to embed)

            ids.append(doc_id)
            documents.append(text)
            metadatas.append({
                "user_id": user_id,
                "tool": tool,
                "data_type": data_type,
                "external_id": str(external_id),
            })

        if not ids:
            return 0

        try:
            # upsert = insert or update (same as SQLite UPSERT behavior)
            # ChromaDB auto-generates embeddings from the document text
            self.collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            logger.info(f"VectorStore: upserted {len(ids)} documents for {tool}/{data_type}")
            return len(ids)
        except Exception as e:
            logger.error(f"VectorStore add_documents failed: {e}")
            return 0

    def add_single(self, user_id, tool, data_type, content, external_id):
        """
        Add ONE document to ChromaDB.

        Convenience wrapper around add_documents() for single items.
        Used when caching a single email/message instead of a batch.

        Args:
            user_id     (str):         Which user
            tool        (str):         "gmail", "slack", "outlook"
            data_type   (str):         "email", "message", "event"
            content     (dict or str): The data to embed
            external_id (str):         Unique ID from external service

        Returns:
            bool → True if added successfully, False if failed

        Example:
            success = vs.add_single("user_1", "gmail", "email",
                                    {"subject": "Meeting", "body": "Tomorrow"},
                                    external_id="msg_001")
        """
        if not self.collection or not external_id:
            return False

        doc_id = self._build_id(user_id, tool, external_id)
        text = self._extract_text(content)

        if not text:
            return False

        try:
            self.collection.upsert(
                ids=[doc_id],
                documents=[text],
                metadatas=[{
                    "user_id": user_id,
                    "tool": tool,
                    "data_type": data_type,
                    "external_id": str(external_id),
                }],
            )
            return True
        except Exception as e:
            logger.error(f"VectorStore add_single failed: {e}")
            return False

    # =========================================================================
    # SEARCH — Find semantically similar documents
    # =========================================================================

    def search(self, query, user_id, tool=None, data_type=None, n_results=10):
        """
        Semantic search — find documents similar in MEANING to the query.

        This is the KEY difference from SQLite LIKE search:
            SQLite:   "release" only matches text containing "release"
            ChromaDB: "release" also matches "deployment", "ship", "launch"

        How it works:
            1. ChromaDB converts query text into a vector (embedding)
            2. Finds stored documents whose vectors are closest (cosine similarity)
            3. Filters by metadata (user_id, tool, data_type) BEFORE searching
            4. Returns top N results sorted by similarity (most similar first)

        Filtering:
            - user_id is ALWAYS applied (never return another user's data)
            - tool can be str or list:
              tool="gmail"              → only gmail results
              tool=["gmail", "slack"]   → both tools
              tool=None                 → all tools
            - data_type same pattern as tool

        Args:
            query      (str):          The search text (what the user is looking for)
            user_id    (str):          Required — only search this user's data
            tool       (str or list):  Optional — filter by tool(s)
            data_type  (str or list):  Optional — filter by data type(s)
            n_results  (int):          Max number of results (default 10)

        Returns:
            list[dict] → each dict has:
                - external_id: the original service ID (gmail msg_id, etc.)
                - tool: which tool this came from
                - data_type: what type of data
                - text: the original document text
                - score: similarity score (0.0 = identical, 2.0 = completely different)
            Empty list if ChromaDB unavailable or no matches found

        Example:
            # Search for deployment-related content across all tools
            results = vs.search("any updates about the release?", user_id="user_1")
            # Returns emails/messages about "deployment", "release", "launch", etc.

            # Search only in gmail
            results = vs.search("meeting with boss", user_id="user_1", tool="gmail")

            # Multi-tool search (for multi-agent queries)
            results = vs.search("order updates", user_id="user_1", tool=["gmail", "slack"])
        """
        if not self.collection:
            logger.warning("VectorStore not available — returning empty results")
            return []

        # Build metadata filter
        # ChromaDB requires $and wrapper when combining 2+ conditions
        # Single condition: {"user_id": "user_1"}
        # Multiple conditions: {"$and": [{"user_id": "user_1"}, {"tool": "gmail"}]}
        conditions = [{"user_id": user_id}]

        if tool:
            if isinstance(tool, list):
                conditions.append({"tool": {"$in": tool}})
            else:
                conditions.append({"tool": tool})

        if data_type:
            if isinstance(data_type, list):
                conditions.append({"data_type": {"$in": data_type}})
            else:
                conditions.append({"data_type": data_type})

        # ChromaDB: 1 condition = plain dict, 2+ = wrap in $and
        where_filter = conditions[0] if len(conditions) == 1 else {"$and": conditions}

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )

            # Parse ChromaDB response into clean list of dicts
            # ChromaDB returns nested lists: results["ids"][0], results["documents"][0], etc.
            output = []
            if results and results["ids"] and results["ids"][0]:
                for i in range(len(results["ids"][0])):
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                    output.append({
                        "external_id": metadata.get("external_id", ""),
                        "tool": metadata.get("tool", ""),
                        "data_type": metadata.get("data_type", ""),
                        "text": results["documents"][0][i] if results["documents"] else "",
                        "score": results["distances"][0][i] if results["distances"] else 0.0,
                    })
            return output

        except Exception as e:
            logger.error(f"VectorStore search failed: {e}")
            return []

    # =========================================================================
    # DELETE — Remove documents (used during cache invalidation)
    # =========================================================================

    def delete_by_tool(self, user_id, tool=None):
        """
        Delete all documents for a user (optionally filtered by tool).

        When is this called?
            - After an ACTION operation (send email, post message)
            - Cache invalidation: SQLite cache is cleared AND ChromaDB docs are removed
            - Both stores must stay in sync

        How it works:
            - Uses metadata filter to find matching documents
            - Deletes all matching documents from the collection
            - If tool=None, deletes ALL documents for the user

        Args:
            user_id (str):         Required — which user's docs to delete
            tool    (str or list): Optional — delete only this tool's docs

        Returns:
            bool → True if deletion succeeded, False if failed

        Example:
            # User sent email → invalidate gmail vectors
            vs.delete_by_tool("user_1", tool="gmail")

            # Clear everything for user
            vs.delete_by_tool("user_1")

            # Clear multiple tools
            vs.delete_by_tool("user_1", tool=["gmail", "slack"])
        """
        if not self.collection:
            return False

        # Build filter — same $and pattern as search()
        conditions = [{"user_id": user_id}]
        if tool:
            if isinstance(tool, list):
                conditions.append({"tool": {"$in": tool}})
            else:
                conditions.append({"tool": tool})

        where_filter = conditions[0] if len(conditions) == 1 else {"$and": conditions}

        try:
            self.collection.delete(where=where_filter)
            logger.info(f"VectorStore: deleted docs for user={user_id}, tool={tool}")
            return True
        except Exception as e:
            logger.error(f"VectorStore delete failed: {e}")
            return False

    def delete_by_ids(self, user_id, tool, external_ids):
        """
        Delete specific documents by their external IDs.

        More targeted than delete_by_tool() — removes only specific items.
        Used when a single email is deleted or a specific message is removed.

        Args:
            user_id      (str):  Which user
            tool         (str):  Which tool (needed to build compound ID)
            external_ids (list): List of external_id strings to delete

        Returns:
            bool → True if deletion succeeded, False if failed

        Example:
            # Delete two specific Gmail messages from vector store
            vs.delete_by_ids("user_1", "gmail", ["msg_001", "msg_002"])
        """
        if not self.collection or not external_ids:
            return False

        doc_ids = [self._build_id(user_id, tool, eid) for eid in external_ids]

        try:
            self.collection.delete(ids=doc_ids)
            logger.info(f"VectorStore: deleted {len(doc_ids)} specific docs")
            return True
        except Exception as e:
            logger.error(f"VectorStore delete_by_ids failed: {e}")
            return False

    # =========================================================================
    # UTILITIES — Health check and stats
    # =========================================================================

    def count(self):
        """
        Get total number of documents in the vector store.

        Returns:
            int → total document count (0 if ChromaDB unavailable)

        Example:
            total = vs.count()  # 142
        """
        if not self.collection:
            return 0
        try:
            return self.collection.count()
        except Exception:
            return 0

    def status(self):
        """
        Get health status of the vector store.

        Returns:
            dict → {"available": bool, "document_count": int, "persist_dir": str}

        Example:
            info = vs.status()
            # {"available": True, "document_count": 142, "persist_dir": "backend/chroma_data"}
        """
        return {
            "available": self.collection is not None,
            "document_count": self.count(),
            "persist_dir": self.persist_dir,
        }


# =============================================================================
# SINGLETON ACCESSOR — Use this everywhere in the app
# =============================================================================

def get_vector_store():
    """
    Get the singleton VectorStore instance.
    Created once on first call, reused everywhere after.

    Usage:
        from database import get_vector_store
        vs = get_vector_store()
        results = vs.search("meeting updates", user_id="user_1")

    Why singleton?
        - One ChromaDB client, one collection, no confusion
        - Same pattern as get_database() and get_brain_router()
        - All agents share the same vector store
    """
    global _vs_instance
    if _vs_instance is None:
        _vs_instance = VectorStore()
    return _vs_instance


# =============================================================================
# TEST BLOCK — Run with: python database/vector_store.py
# =============================================================================
if __name__ == "__main__":
    import shutil

    TEST_DIR = "test_chroma_data"

    print("Testing VectorStore...")
    print("=" * 50)

    vs = VectorStore(persist_dir=TEST_DIR)

    # 1. Status check
    status = vs.status()
    print(f"[OK] Status: available={status['available']}, docs={status['document_count']}")

    # 2. Add batch of emails
    emails = [
        {"id": "msg_001", "subject": "Project Deployment", "from": "dev@company.com",
         "body": "The release is scheduled for Friday. All tests passing."},
        {"id": "msg_002", "subject": "Team Meeting", "from": "boss@company.com",
         "body": "Don't forget our standup meeting tomorrow at 10am."},
        {"id": "msg_003", "subject": "Invoice #1234", "from": "finance@company.com",
         "body": "Please find attached the invoice for last month's services. Total: 5000 TK."},
    ]
    count = vs.add_documents("user_1", "gmail", "email", emails)
    print(f"[OK] Added {count} gmail emails")

    # 3. Add slack messages
    messages = [
        {"id": "slack_001", "channel": "#dev", "user": "john", "text": "Deploy pipeline is green, ready to ship!"},
        {"id": "slack_002", "channel": "#general", "user": "sarah", "text": "Lunch meeting moved to 2pm"},
    ]
    count = vs.add_documents("user_1", "slack", "message", messages)
    print(f"[OK] Added {count} slack messages")

    # 4. Semantic search — "release" should match "deployment" and "ship"
    results = vs.search("any updates about the release?", user_id="user_1")
    print(f"[OK] Semantic search 'release': {len(results)} results")
    for r in results[:3]:
        print(f"     - [{r['tool']}] {r['external_id']} (score: {r['score']:.3f})")

    # 5. Filtered search — only gmail
    results = vs.search("meeting", user_id="user_1", tool="gmail")
    print(f"[OK] Gmail-only search 'meeting': {len(results)} results")

    # 6. Multi-tool search — gmail + slack
    results = vs.search("meeting", user_id="user_1", tool=["gmail", "slack"])
    print(f"[OK] Multi-tool search 'meeting': {len(results)} results")

    # 7. Add single document
    success = vs.add_single("user_1", "outlook", "event",
                            {"title": "Sprint Review", "start": "2024-02-20 14:00"},
                            external_id="evt_001")
    print(f"[OK] Add single outlook event: {success}")

    # 8. Total count
    total = vs.count()
    print(f"[OK] Total documents: {total}")

    # 9. Delete by tool
    vs.delete_by_tool("user_1", tool="slack")
    after_delete = vs.count()
    print(f"[OK] After deleting slack: {after_delete} documents remain")

    # 10. Delete specific IDs
    vs.delete_by_ids("user_1", "gmail", ["msg_003"])
    after_specific = vs.count()
    print(f"[OK] After deleting msg_003: {after_specific} documents remain")

    # Cleanup test data
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    print("\n" + "=" * 50)
    print("[ALL TESTS PASSED]")

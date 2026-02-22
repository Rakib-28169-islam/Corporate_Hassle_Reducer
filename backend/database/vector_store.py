"""ChromaDB vector store for semantic search over cached tool data."""

import os
import json
import logging

logger = logging.getLogger(__name__)

CHROMA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "chroma_data"
)
COLLECTION_NAME = "hassle_reducer_memory"

_vs_instance = None


class VectorStore:
    """ChromaDB-backed semantic search, singleton via get_vector_store()."""

    def __init__(self, persist_dir=None):
        """Initialize ChromaDB client. Falls back gracefully if unavailable."""
        self.persist_dir = persist_dir or CHROMA_DIR
        self.collection = None

        try:
            import chromadb
            self.client = chromadb.PersistentClient(path=self.persist_dir)
            self.collection = self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info(f"VectorStore initialized at {self.persist_dir} "
                        f"(documents: {self.collection.count()})")
        except Exception as e:
            logger.warning(f"ChromaDB failed to initialize: {e}")
            logger.warning("Semantic search will be unavailable. SQLite keyword search still works.")
            self.client = None

    def _build_id(self, user_id, tool, external_id):
        """Build compound doc ID: '{user_id}__{tool}__{external_id}'."""
        return f"{user_id}__{tool}__{external_id}"

    def _extract_text(self, content):
        """Flatten a dict or JSON string into searchable plain text."""
        if isinstance(content, dict):
            parts = []
            for key, value in content.items():
                if value and isinstance(value, str):
                    parts.append(f"{key}: {value}")
                elif value:
                    parts.append(f"{key}: {str(value)}")
            return " ".join(parts).strip()
        elif isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return self._extract_text(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
            return content.strip()
        return ""

    def _build_where_filter(self, user_id, tool=None, data_type=None):
        """Build a ChromaDB metadata where-filter dict.

        Single condition returns a plain dict; 2+ conditions
        are wrapped in {"$and": [...]}.
        """
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

        return conditions[0] if len(conditions) == 1 else {"$and": conditions}

    def add_documents(self, user_id, tool, data_type, items, id_field="id"):
        """Upsert multiple documents into ChromaDB for semantic search.

        Returns the number of documents successfully added/updated.
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
                continue

            doc_id = self._build_id(user_id, tool, external_id)
            text = self._extract_text(item)

            if not text:
                continue

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
        """Add one document to ChromaDB. Returns True on success."""
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

    def search(self, query, user_id, tool=None, data_type=None, n_results=10):
        """Semantic search: find documents similar in meaning to query.

        Returns list of dicts with keys:
        external_id, tool, data_type, text, score.
        """
        if not self.collection:
            logger.warning("VectorStore not available — returning empty results")
            return []

        where_filter = self._build_where_filter(user_id, tool=tool, data_type=data_type)

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )

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

    def delete_by_tool(self, user_id, tool=None):
        """Delete all documents for a user, optionally filtered by tool."""
        if not self.collection:
            return False

        where_filter = self._build_where_filter(user_id, tool=tool)

        try:
            self.collection.delete(where=where_filter)
            logger.info(f"VectorStore: deleted docs for user={user_id}, tool={tool}")
            return True
        except Exception as e:
            logger.error(f"VectorStore delete failed: {e}")
            return False

    def delete_by_ids(self, user_id, tool, external_ids):
        """Delete specific documents by their external IDs."""
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

    def delete_by_user(self, user_id):
        """Delete ALL documents for a user across all tools. Returns deleted count."""
        if not self.collection:
            return 0

        where_filter = self._build_where_filter(user_id)
        try:
            # Get count before deletion
            existing = self.collection.get(where=where_filter, include=[])
            count = len(existing["ids"]) if existing and existing["ids"] else 0
            if count > 0:
                self.collection.delete(where=where_filter)
                logger.info(f"VectorStore: deleted {count} docs for user={user_id}")
            return count
        except Exception as e:
            logger.error(f"VectorStore delete_by_user failed: {e}")
            return 0

    def count(self):
        """Return total document count (0 if unavailable)."""
        if not self.collection:
            return 0
        try:
            return self.collection.count()
        except Exception:
            return 0

    def status(self):
        """Return health dict: available, document_count, persist_dir."""
        return {
            "available": self.collection is not None,
            "document_count": self.count(),
            "persist_dir": self.persist_dir,
        }


def get_vector_store():
    """Get or create the singleton VectorStore instance."""
    global _vs_instance
    if _vs_instance is None:
        _vs_instance = VectorStore()
    return _vs_instance

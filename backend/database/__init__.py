"""
=============================================================================
DATABASE PACKAGE — All data storage lives here
=============================================================================

This package contains:
    sqlite_manager.py  → SQLite database (keyword search, caching, chat history)
    vector_store.py    → ChromaDB vector database (semantic/meaning search)

USAGE:
    # Import from the package directly — clean, simple
    from database import get_database, get_vector_store

    # SQLite — keyword search, caching
    db = get_database()
    await db.init_db()
    await db.cache_data("user_1", "gmail", "email", {...}, external_id="msg_001")
    results = await db.search_memory("user_1", tool="gmail", query="meeting")

    # ChromaDB — semantic search
    vs = get_vector_store()
    vs.add_documents("user_1", "gmail", "email", [items])
    results = vs.search("any updates about release?", user_id="user_1")

SEARCH FALLBACK CHAIN:
    1. SQLite keyword search    (3ms, free)     → sqlite_manager.py
    2. ChromaDB semantic search (40ms, free)    → vector_store.py
    3. Composio API call        (1500ms, costly) → tools/*.py

    Smart threshold: if SQLite returns >= 3 results, skip ChromaDB.
=============================================================================
"""

from database.sqlite_manager import DatabaseManager, get_database
from database.vector_store import VectorStore, get_vector_store

__all__ = [
    "DatabaseManager",
    "get_database",
    "VectorStore",
    "get_vector_store",
]

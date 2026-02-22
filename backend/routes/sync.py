"""Sync routes — data sync endpoints after OAuth and periodic refresh."""

import logging
from fastapi import APIRouter, HTTPException, Query

from database import get_database
from database.vector_store import get_vector_store
from services.data_fetch_service import get_data_fetch_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/on-connected")
async def on_tool_connected(
    tool_name: str = Query(..., description="Tool that was connected"),
    user_id: str = Query("default", description="User ID"),
    connection_id: str = Query(None, description="Composio connection ID"),
):
    """Called by frontend after OAuth completes. Creates user, saves connection, fetches data."""
    tool_name = tool_name.lower()
    if tool_name not in ("gmail", "slack", "outlook"):
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")

    db = get_database()

    await db.create_user(user_id, name=user_id)

    conn_id = connection_id or f"{user_id}_{tool_name}"
    await db.save_connection(
        connection_id=conn_id, user_id=user_id, tool=tool_name,
        composio_id=connection_id, status="connected",
    )

    fetch = get_data_fetch_service()
    result = await fetch.initial_fetch(user_id, tool_name)

    logger.info(f"[Sync] on-connected: {user_id}/{tool_name} -> {result['count']} items")

    return {
        "status": result["status"], "tool": tool_name,
        "count": result["count"], "user_id": user_id,
        "error": result.get("error"),
    }


@router.get("/status/{user_id}")
async def get_sync_status(user_id: str):
    """Get sync status for a user — cached data count per tool."""
    db = get_database()

    connections = await db.get_connections(user_id)
    conn_map = {c["tool"]: c for c in connections}

    status = {}
    for tool in ("gmail", "slack", "outlook"):
        conn = conn_map.get(tool)
        cached = await db.get_memory_count(user_id, tool=tool)

        status[tool] = {
            "connected": conn["status"] == "connected" if conn else False,
            "connection_status": conn["status"] if conn else "not_connected",
            "cached_count": cached,
        }

    return {"user_id": user_id, "tools": status}


@router.post("/refresh/{tool_name}")
async def force_refresh(
    tool_name: str,
    user_id: str = Query("default", description="User ID"),
):
    """Force re-fetch: invalidate cache then re-fetch from Composio API."""
    tool_name = tool_name.lower()
    if tool_name not in ("gmail", "slack", "outlook"):
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")

    from services.sync_service import get_sync_service
    sync = get_sync_service()
    await sync.force_refresh(user_id, tool_name)

    fetch = get_data_fetch_service()
    result = await fetch.initial_fetch(user_id, tool_name)

    return {
        "status": result["status"], "tool": tool_name,
        "count": result["count"], "user_id": user_id,
    }


@router.delete("/user/{user_id}")
async def delete_user_data(user_id: str):
    """Delete ALL data for a user — memory, connections, chat history, user record."""
    db = get_database()

    deleted = {}
    for table, col in [
        ("memory", "user_id"),
        ("connections", "user_id"),
        ("chat_history", "user_id"),
        ("users", "id"),
    ]:
        try:
            count = await db.delete_by_user(table, col, user_id)
            deleted[table] = count
        except Exception as e:
            logger.error(f"[Sync] Failed to delete {table} for {user_id}: {e}")
            deleted[table] = f"error: {e}"

    # Also clear vector store for this user
    try:
        vs = get_vector_store()
        vs_count = vs.delete_by_user(user_id)
        deleted["vector_store"] = vs_count
    except Exception as e:
        logger.error(f"[Sync] Failed to clear vector store for {user_id}: {e}")
        deleted["vector_store"] = f"error: {e}"

    logger.info(f"[Sync] Deleted all data for user {user_id}: {deleted}")
    return {"user_id": user_id, "deleted": deleted}

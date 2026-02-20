"""
=============================================================================
SYNC ROUTES — Data Sync Endpoints
=============================================================================

Endpoints for triggering data sync after OAuth and checking sync status.

    POST /api/sync/on-connected  → Trigger initial fetch after OAuth completes
    GET  /api/sync/status/{uid}  → Check sync status per tool
    POST /api/sync/refresh/{tool}→ Force re-fetch all data for a tool

=============================================================================
"""

import logging
from fastapi import APIRouter, HTTPException, Query

from database import get_database
from services.data_fetch_service import get_data_fetch_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/on-connected")
async def on_tool_connected(
    tool_name: str = Query(..., description="Tool that was connected"),
    user_id: str = Query("default", description="User ID"),
    connection_id: str = Query(None, description="Composio connection ID"),
):
    """
    Called by frontend after OAuth completes successfully.

    Flow:
        1. Create user in local DB if new
        2. Save/update connection status to 'connected'
        3. Run initial_fetch (blocking ~3-5s) — fetches recent data
        4. Return count of items synced

    The frontend should call this AFTER polling /api/auth/status/{id}
    returns status=ACTIVE.
    """
    tool_name = tool_name.lower()
    if tool_name not in ("gmail", "slack", "outlook"):
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")

    db = get_database()

    # 1. Create user if new (idempotent — returns False if exists)
    await db.create_user(user_id, name=user_id)

    # 2. Save connection as 'connected'
    conn_id = connection_id or f"{user_id}_{tool_name}"
    await db.save_connection(
        connection_id=conn_id,
        user_id=user_id,
        tool=tool_name,
        composio_id=connection_id,
        status="connected",
    )

    # 3. Run initial fetch (blocking — frontend shows spinner)
    fetch = get_data_fetch_service()
    result = await fetch.initial_fetch(user_id, tool_name)

    logger.info(f"[Sync] on-connected: {user_id}/{tool_name} "
                f"→ {result['count']} items")

    return {
        "status": result["status"],
        "tool": tool_name,
        "count": result["count"],
        "user_id": user_id,
        "error": result.get("error"),
    }


@router.get("/status/{user_id}")
async def get_sync_status(user_id: str):
    """
    Get sync status for a user — how much data is cached per tool.

    Returns:
        dict with per-tool info: connected, cached_count
    """
    db = get_database()

    # Get connections
    connections = await db.get_connections(user_id)
    conn_map = {c["tool"]: c for c in connections}

    # Get cached counts per tool
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
    """
    Force re-fetch all data for a tool.

    1. Invalidate existing cache (SQLite + ChromaDB)
    2. Re-fetch from Composio API
    3. Return new count
    """
    tool_name = tool_name.lower()
    if tool_name not in ("gmail", "slack", "outlook"):
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")

    # 1. Invalidate existing cache
    from services.sync_service import get_sync_service
    sync = get_sync_service()
    await sync.force_refresh(user_id, tool_name)

    # 2. Re-fetch
    fetch = get_data_fetch_service()
    result = await fetch.initial_fetch(user_id, tool_name)

    return {
        "status": result["status"],
        "tool": tool_name,
        "count": result["count"],
        "user_id": user_id,
    }

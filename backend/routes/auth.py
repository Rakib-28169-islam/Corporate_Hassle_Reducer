import os
import logging
from fastapi import APIRouter, HTTPException
from composio_client import Composio
from dotenv import load_dotenv
from database import get_database

load_dotenv()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

composio = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

# Mapping tool names to Composio toolkit slugs and auth config IDs
# Auth config IDs are fetched dynamically from Composio on first use
TOOL_CONFIG = {
    "gmail": {"toolkit_slug": "gmail", "auth_config_id": None},
    "slack": {"toolkit_slug": "slack", "auth_config_id": None},
    "outlook": {"toolkit_slug": "outlook", "auth_config_id": None},
}


def _get_auth_config_id(tool_name: str) -> str:
    """Get or fetch the auth_config_id for a tool from Composio."""
    config = TOOL_CONFIG.get(tool_name)
    if not config:
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")

    if config["auth_config_id"]:
        return config["auth_config_id"]

    # Fetch auth configs from Composio for this toolkit
    try:
        auth_configs = composio.auth_configs.list(
            toolkit_slug=config["toolkit_slug"]
        )
        if auth_configs.items:
            config["auth_config_id"] = auth_configs.items[0].id
            return config["auth_config_id"]
        else:
            raise HTTPException(
                status_code=404,
                detail=f"No auth config found for {tool_name}. Set it up in Composio dashboard first.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch auth config for {tool_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connect/{tool_name}")
async def connect_tool(tool_name: str, user_id: str = "default"):
    """
    Initiate OAuth connection for a tool.
    Returns the OAuth redirect URL for the frontend to open.
    """
    tool_name = tool_name.lower()
    auth_config_id = _get_auth_config_id(tool_name)

    try:
        connection_request = composio.link.create(
            user_id=user_id,
            auth_config_id=auth_config_id,
            callback_url="http://localhost:5173/auth/callback",
        )

        connection_id = connection_request.connected_account_id

        # Save pending connection to local DB for tracking
        db = get_database()
        await db.save_connection(
            connection_id=connection_id,
            user_id=user_id,
            tool=tool_name,
            composio_id=connection_id,
            status="pending",
        )

        return {
            "connection_id": connection_id,
            "redirect_url": connection_request.redirect_url,
            "status": "INITIATED",
        }
    except Exception as e:
        logger.error(f"Failed to initiate connection for {tool_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_all_connections_status(user_id: str = "default"):
    """
    Get connection status for all tools for a user.
    Returns status of Gmail, Slack, and Outlook connections.
    """
    try:
        accounts = composio.connected_accounts.list(user_ids=[user_id])

        connections = {}
        for account in accounts.items:
            toolkit = account.toolkit.slug if account.toolkit else "unknown"
            connections[toolkit] = {
                "id": account.id,
                "status": account.status,
                "toolkit": toolkit,
            }

        # Build response with all 3 tools
        result = {}
        for tool_name in TOOL_CONFIG:
            if tool_name in connections:
                result[tool_name] = connections[tool_name]
            else:
                result[tool_name] = {
                    "id": None,
                    "status": "NOT_CONNECTED",
                    "toolkit": tool_name,
                }

        return result
    except Exception as e:
        logger.error(f"Failed to get connections status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{connection_id}")
async def get_connection_status(connection_id: str):
    """
    Get status of a single connection (used for polling after OAuth redirect).
    """
    try:
        account = composio.connected_accounts.retrieve(connection_id)
        toolkit = account.toolkit.slug if account.toolkit else "unknown"
        return {
            "id": account.id,
            "status": account.status,
            "toolkit": toolkit,
        }
    except Exception as e:
        logger.error(f"Failed to get connection {connection_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect/{connection_id}")
async def disconnect_tool(connection_id: str):
    """
    Disconnect (delete) a connected account.
    """
    try:
        composio.connected_accounts.delete(connection_id)
        return {"status": "disconnected", "connection_id": connection_id}
    except Exception as e:
        logger.error(f"Failed to disconnect {connection_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

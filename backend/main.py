import sys
import os
import json
import logging
import traceback

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from routes.auth import router as auth_router
from routes.sync import router as sync_router
from agents.supervisor_agent import SupervisorAgent
from database import get_database, get_vector_store
from services.data_fetch_service import get_data_fetch_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Corporate Hassle Reducer API")

# CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(auth_router)
app.include_router(sync_router)


# ==================== STARTUP ====================

@app.on_event("startup")
async def startup():
    """Initialize database, vector store, and background sync on server start."""
    # 1. SQLite — create tables if not exist
    db = get_database()
    await db.init_db()
    logger.info("SQLite database ready")

    # 2. ChromaDB — initialize vector store
    vs = get_vector_store()
    logger.info(f"VectorStore ready: {vs.status()}")

    # 3. Start background data sync (checks for new data every 60s)
    fetch_service = get_data_fetch_service()
    await fetch_service.start_periodic_sync(interval_seconds=60)
    logger.info("Background data sync started (60s interval)")


@app.on_event("shutdown")
async def shutdown():
    """Stop background tasks on server shutdown."""
    fetch_service = get_data_fetch_service()
    await fetch_service.stop_periodic_sync()
    logger.info("Background data sync stopped")

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        self.active_connections.pop(user_id, None)

    async def send_json(self, user_id: str, data: dict):
        ws = self.active_connections.get(user_id)
        if ws:
            await ws.send_text(json.dumps(data))

manager = ConnectionManager()

# Lazy-loaded supervisor agents per user
_supervisors: dict[str, SupervisorAgent] = {}

def get_supervisor(user_id: str) -> SupervisorAgent:
    if user_id not in _supervisors:
        _supervisors[user_id] = SupervisorAgent(user_id=user_id)
    return _supervisors[user_id]


@app.get("/")
def read_root():
    return {"status": "active", "message": "Corporate Hassle Reducer API"}


@app.get("/health")
async def health_check():
    """Health check — shows DB tables, vector store status, brain status."""
    db = get_database()
    vs = get_vector_store()

    return {
        "status": "healthy",
        "database": await db.get_table_info(),
        "vector_store": vs.status(),
    }


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(user_id, websocket)
    try:
        await manager.send_json(user_id, {
            "type": "system",
            "message": f"Connected! Welcome, {user_id}."
        })

        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                query = payload.get("message", data)
            except json.JSONDecodeError:
                query = data

            # Send "thinking" indicator
            await manager.send_json(user_id, {
                "type": "thinking",
                "message": "Processing your request..."
            })

            # Route through SupervisorAgent (async LangGraph pipeline)
            try:
                supervisor = get_supervisor(user_id)
                result = await supervisor.arun(query)

                # answer_node already formats the WebSocket response
                await manager.send_json(user_id, result)
            except Exception as e:
                logger.error(f"Agent error: {traceback.format_exc()}")
                await manager.send_json(user_id, {
                    "type": "error",
                    "message": f"Agent error: {str(e)}"
                })

    except WebSocketDisconnect:
        manager.disconnect(user_id)
        logger.info(f"User {user_id} disconnected")
    except Exception as e:
        manager.disconnect(user_id)
        logger.error(f"WebSocket error for {user_id}: {e}")


# To run: uvicorn main:app --reload

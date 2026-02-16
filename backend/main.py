from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json

app = FastAPI(title="Corporate Hassle Reducer API")

# 1. Allow React to talk to us (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"], # React Default Port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. The WebSocket Manager (Stores active connections)
class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

manager = ConnectionManager()

# --- ROUTES ---

@app.get("/")
def read_root():
    return {"status": "active", "message": "Brain is Online 🧠   hi  rokib + esha" }

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """
    The Real-Time Channel.
    Frontend connects here to get live email updates.
    """
    await manager.connect(websocket)
    try:
        await manager.send_personal_message(json.dumps({
            "type": "system",
            "message": f"Connected! Welcome user {user_id}"
        }), websocket)

        # --- SIMULATION: BOOTSTRAP SYNC ---
        # This simulates fetching 100 emails in chunks
        for i in range(1, 101, 20):
            await asyncio.sleep(1) # Fake delay
            await manager.send_personal_message(json.dumps({
                "type": "progress",
                "percent": i,
                "status": f"Fetched {i} emails..."
            }), websocket)

        await manager.send_personal_message(json.dumps({
            "type": "complete",
            "message": "Sync Finished! ✅"
        }), websocket)

        # Keep connection open for chat
        while True:
            data = await websocket.receive_text()
            # Echo back (We will hook up the Agent here later)
            await manager.send_personal_message(f"You said: {data}", websocket)

    except Exception as e:
        manager.disconnect(websocket)
        print(f"User {user_id} disconnected")

# To run: uvicorn main:app --reload
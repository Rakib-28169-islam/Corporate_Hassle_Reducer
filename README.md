# Corporate Hassle Reducer

An AI-powered corporate assistant that integrates Gmail, Slack, and Outlook through natural language queries. Ask questions like "show my unread emails", "emails from Farid", or "summarize last week's messages" and get instant results.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, WebSocket |
| AI/LLM | LangChain, LangGraph, Google Gemini 2.0 Flash, Groq Llama 3.3 70B |
| Database | SQLite (structured data), ChromaDB (vector/semantic search) |
| Integrations | Composio SDK (Gmail, Slack, Outlook OAuth) |
| Frontend | React 18, Vite, Tailwind CSS |

## Project Structure

```
Corporate_Hassle_Reducer/
├── backend/
│   ├── main.py                  # FastAPI app + WebSocket endpoint
│   ├── requirements.txt         # Python dependencies
│   ├── .env.example             # Environment variable template
│   ├── agents/                  # AI agents (Supervisor, Gmail, Slack, Outlook)
│   ├── core/                    # LLM manager, workflow graph, query parser
│   ├── database/                # SQLite manager, ChromaDB vector store
│   ├── routes/                  # REST API routes (auth, sync)
│   ├── services/                # Data fetch & sync services
│   ├── tools/                   # Composio tool wrappers, local search tools
│   └── test/                    # Test files
├── frontend/
│   ├── src/                     # React source code
│   ├── package.json             # Node.js dependencies
│   └── .env.example             # Frontend env template
└── README.md
```

## Prerequisites

- **Python 3.11+** — [Download](https://www.python.org/downloads/)
- **Node.js 18+** — [Download](https://nodejs.org/)
- **Git** — [Download](https://git-scm.com/downloads)

## API Keys Required

You need **3 free API keys** before running:

| Key | Where to Get | Free Tier |
|-----|-------------|-----------|
| Google Gemini API Key | [Google AI Studio](https://aistudio.google.com/apikey) | Yes (generous) |
| Groq API Key | [Groq Console](https://console.groq.com/keys) | Yes (30 req/min) |
| Composio API Key | [Composio Dashboard](https://app.composio.dev/settings) | Yes |

## Setup Instructions

### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd Corporate_Hassle_Reducer
```

### Step 2: Backend Setup

```bash
# Navigate to backend
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows (Command Prompt):
venv\Scripts\activate
# Windows (PowerShell):
venv\Scripts\Activate.ps1
# Windows (Git Bash):
source venv/Scripts/activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Step 3: Configure Backend Environment

```bash
# Copy the example env file
# Windows (Command Prompt):
copy .env.example .env
# macOS/Linux/Git Bash:
cp .env.example .env
```

Open `backend/.env` in a text editor and fill in your API keys:

```env
GOOGLE_API_KEY=your_actual_google_api_key
GROQ_API_KEY=your_actual_groq_api_key
COMPOSIO_API_KEY=your_actual_composio_api_key
```

**Important:** Do NOT use quotes around values in the `.env` file.

### Step 4: Composio Setup (Gmail/Slack/Outlook)

Before connecting tools, you need to set up auth configs in Composio:

1. Go to [Composio Dashboard](https://app.composio.dev)
2. Navigate to **Toolkits** > **Gmail** > Click **Setup Integration**
3. Follow the OAuth setup (uses Google OAuth consent screen)
4. Repeat for **Slack** and **Outlook** if needed

### Step 5: Frontend Setup

```bash
# Open a new terminal, navigate to frontend
cd frontend

# Install dependencies
npm install
```

Create the frontend env file:

```bash
# Windows (Command Prompt):
copy .env.example .env
# macOS/Linux/Git Bash:
cp .env.example .env
```

The default values (`localhost:8000`) should work without changes.

### Step 6: Run the Application

You need **two terminals** running simultaneously:

**Terminal 1 — Backend:**
```bash
cd backend
# Activate venv first (see Step 2)
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 — Frontend:**
```bash
cd frontend
npm run dev
```

### Step 7: Open the App

1. Open your browser and go to: **http://localhost:5173**
2. Connect your Gmail/Slack/Outlook accounts using the connection buttons
3. After connecting, the app will auto-fetch your emails/messages
4. Start chatting with the AI assistant via the chat interface

## How It Works

```
User Query → WebSocket → Understand Node → Execute → Answer
                              │
                    ┌─────────┴──────────┐
                    │  Keyword Fast-Path  │  (simple queries, 0ms)
                    │         OR          │
                    │  LLM Structured Out │  (complex queries, ~1-3s)
                    └─────────┬──────────┘
                              │
                     ParsedIntent (validated)
                              │
                    ┌─────────┴──────────┐
                    │  SQLite (structured)│
                    │  ChromaDB (semantic)│
                    │  Hybrid (both)      │
                    └────────────────────┘
```

- **Simple queries** ("show my emails", "hello") hit the keyword fast-path with zero LLM calls
- **Complex queries** ("emails from Farid about budget") use one LLM call for structured intent extraction
- **All SQL is deterministic** — LLM never generates SQL, only extracts filters/fields

## API Endpoints

| Method | Endpoint | Description |
|--------|---------|-------------|
| GET | `/` | Health check (basic) |
| GET | `/health` | Detailed health (DB + vector store status) |
| WebSocket | `/ws/{user_id}` | Chat interface (main communication) |
| POST | `/api/auth/connect/{tool_name}` | Start OAuth flow for gmail/slack/outlook |
| GET | `/api/auth/status?user_id=default` | Get connection status for all tools |
| GET | `/api/auth/status/{connection_id}` | Get single connection status |
| POST | `/api/auth/disconnect/{connection_id}` | Disconnect a tool |
| POST | `/api/sync/on-connected` | Trigger data fetch after OAuth |
| GET | `/api/sync/status/{user_id}` | Get cached data counts per tool |
| POST | `/api/sync/refresh/{tool_name}` | Force re-fetch data |
| DELETE | `/api/sync/user/{user_id}` | Delete all user data |

## Troubleshooting

### "No AI brains available" error
Your API keys are missing or invalid. Check `backend/.env` has correct `GOOGLE_API_KEY` and `GROQ_API_KEY`.

### "No auth config found" when connecting Gmail
You haven't set up the Gmail integration in Composio dashboard. Go to [Composio](https://app.composio.dev) > Toolkits > Gmail > Setup Integration.

### ChromaDB PermissionError on Windows
This is a known Windows issue with locked SQLite files. It's harmless and only affects test cleanup — the app works fine.

### Frontend can't connect to backend
Make sure both servers are running. Check that `frontend/.env` has `VITE_API_URL=http://localhost:8000`.

### pip install fails on Windows
Make sure you're using Python 3.11+. Some packages need Visual C++ Build Tools. Install from [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/).

## Running Tests

```bash
cd backend

# Activate virtual environment first, then:
python test/test_phase8_understand.py    # 79 tests (understanding pipeline)
python test/test_phase4_workflow.py       # 37 tests (workflow integration)
```

# tallyAutomation-backend

A FastAPI-based backend for EazyBooks - an AI-powered Chartered Accountant (CA) and Financial Advisor platform with Tally ERP integration.

## Tech Stack

- **Framework**: FastAPI with Uvicorn
- **Language**: Python 3.11
- **Database**: PostgreSQL (psycopg2)
- **Vector DB**: Pinecone
- **Authentication**: Firebase Admin SDK + JWT
- **Cloud Storage**: AWS S3 (boto3, aioboto3)
- **LLM/AI**: OpenAI (gpt-4o-mini), LangChain, LangGraph, Tavily Search
- **Embeddings**: OpenAI text-embedding-3-small, sentence-transformers

## Project Structure

```
├── app/                    # FastAPI app factory and initialization
├── config/                 # Settings and environment configuration
├── finadvisor/            # Financial advisor module
│   ├── AIBots/            # AI bot implementations
│   ├── chatbot/           # Chat service with OpenAI assistants
│   ├── controller/        # API endpoints (chat, auth, profile, kite)
│   ├── prompts/           # System prompts
│   ├── services/          # Business logic (alerts, portfolio, news)
│   └── validation/        # Input validation
├── applicationca/         # Chartered Accountant module
│   ├── agents/            # LangGraph agents (RAG, Tally, data retrieval)
│   ├── aws/               # AWS S3 services
│   ├── chatbot/           # CA-specific chat service
│   ├── contoller/         # API endpoints (auth, chat, files, tally, database)
│   ├── database/          # PostgreSQL operations
│   ├── processingagent/   # Accounting agents (balance sheet, ITR, audit, GST)
│   ├── services/          # Business logic (BM25, financial tools)
│   └── validation/        # Input validation
├── utils/                 # Shared utilities (auth, file processing)
├── run.py                 # Entry point - starts Uvicorn server
├── main.py                # Advanced LangGraph agent workflows
└── requirements.txt       # Python dependencies
```

## Key Commands

```bash
# Start development server
python run.py

# Install dependencies
pip install -r requirements.txt
```

## API Routes

### FinAdvisor (prefix: `/`)
- `POST /chat` - Chat with financial advisor
- `GET/POST /profile` - User profile management
- `/kite/*` - Kite trading integration
- `/auth/*` - Authentication

### ApplicationCA (prefix: `/ca`)
- `POST /auth/email-signup` - User registration
- `POST /auth/email-signin` - User login
- `POST /upload/` - File upload with S3 storage
- `POST /chat` - CA chatbot with RAG
- `GET /search` - Vector database search
- `/database/*` - Database operations
- `/tally/*` - Tally ERP integration (sync, connection check)
- `/files/*` - File management
- `/mis/*` - Management Information System
- `WS /ws/` - WebSocket for real-time communication

## Environment Variables

Required:
```
DATABASE_URL           # PostgreSQL connection string
JWT_SECRET            # JWT signing secret
OPENAI_API_KEY        # OpenAI API key
PINECONE_API_KEY      # Pinecone vector DB key
AWS_ACCESS_KEY_ID     # AWS credentials
AWS_SECRET_ACCESS_KEY
AWS_DEFAULT_REGION
```

Optional:
```
GEMINI_API_KEY        # Google Gemini
TAVILY_API_KEY        # Tavily search
KITE_API_KEY          # Kite trading
NEWS_API_KEY          # News API
CORS_ORIGINS          # Comma-separated origins (default: localhost:3000,5173)
APP_ENV               # development/production
```

Firebase requires `firebase_key.json` in root directory.

## Database Schema

PostgreSQL with tables:
- `users` - User accounts
- `clients` - Client entities linked to users
- `lockr` - Document folders/lockers
- `files` - Documents with S3 references
- `chats` - Chat sessions
- `chunks` - Text chunks with embeddings for vector search

See `README_DB_STRUCTURE.md` for detailed schema.

## Core Features

### ApplicationCA Module
- **Tally ERP Integration**: Real-time data sync via WebSocket and ODBC
- **Document Management**: File upload, S3 storage, vector embeddings
- **Multi-Agent Workflows**: LangGraph agents for:
  - Balance sheet preparation
  - ITR filing
  - GST compliance
  - Audit workflows
  - Financial analysis
  - Tax planning
- **Semantic Search**: Vector retrieval with BM25 + cross-encoder reranking

### FinAdvisor Module
- Portfolio analysis and scoring
- Stock market alerts
- Financial news aggregation (NewsAPI, Reddit, yfinance)
- Chat-based financial advice

## Authentication

- JWT Bearer tokens via HTTPBearer
- Firebase Auth for Google OAuth and email/password
- Token validation in `utils/auth_utils.py`

## Deployment

GitHub Actions workflow (`.github/workflows/deploy.yml`):
- Triggers on push to `main` branch
- Deploys to EC2 (3.109.207.91)
- Uses systemd service `fastapi`

## Mock Tally Server (Development)

For macOS development without actual Tally ERP, use the mock server:

```bash
# Run the mock Tally server (port 9000)
python -m mock_tally

# Or with the convenience script
./run_mock_tally.sh
```

### Mock Server Features
- Simulates all Tally XML API operations
- Pre-populated with realistic Indian business data (90+ ledgers, 7 companies)
- Supports: get-companies, get-ledgers, get-groups, get-inventory, get-godowns
- Supports: create-ledger, edit-ledger, create-group, import-voucher
- Debug endpoints at `/stats`, `/ledgers`, `/companies`, `/reset`

### Mock Server Files
```
mock_tally/
├── server.py          # FastAPI server (port 9000)
├── sample_data.py     # Realistic Indian business data
├── xml_generators.py  # Tally XML response generators
└── README.md          # Detailed documentation
```

## Code Conventions

- Controllers in `contoller/` directories handle HTTP endpoints
- Services contain business logic
- Agents are LangGraph-based with specialized prompts
- Validation rules per task type in `validation/` directories
- Use `config/settings.py` for accessing environment variables

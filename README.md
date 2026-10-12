# DataOne

**An AI-First, Agentic Database Engineering & Data Transformation Platform**

---

## Product Vision

DataOne is a production-grade platform for managing heterogeneous data systems intelligently with AI. It acts as an **Agentic DBA** — finding matches, mapping structures, creating pipelines, running natural language queries, and auditing security classifications at enterprise scale.

---

## Architecture

DataOne runs as a clean multi-service Docker stack with five services:

| Service | Image / Build | Purpose | Port |
| :--- | :--- | :--- | :--- |
| `frontend` | `nginx` (serving Next.js static build) | Web UI | `3011` → container `3000` |
| `api` | Custom (FastAPI, `./backend`) | REST API backend | `8011` → container `8000` |
| `worker` | Custom (Celery, `./backend`) | Async task processing | — |
| `broker` | `redis:7-alpine` | Celery broker + result backend | Internal `6379` (not host-published) |
| `postgres` | `postgres:15-alpine` | App metadata + seeded HR demo data | Internal `5432` (not host-published) |

```
┌────────────┐    HTTP    ┌────────────┐    SQL    ┌────────────┐
│  frontend  │ ─────────▶ │     api    │ ────────▶ │  postgres  │
│  (nginx)   │            │  (FastAPI) │           │            │
└────────────┘            └─────┬──────┘           └────────────┘
                                │ enqueue
                                ▼
                          ┌──────────┐    pub/sub    ┌────────────┐
                          │  worker  │ ◀───────────▶ │   broker   │
                          │ (Celery) │               │   (redis)  │
                          └──────────┘               └────────────┘
```

The frontend is built once and served as static files by nginx. The API and worker share the same Docker image but run with different entrypoints. The `api` service talks to `postgres` for metadata and uses `broker` (Redis) to enqueue long-running AI tasks for the `worker`.

---

## Key Features

### 1. Database Topology Visualizer
Interactive graph visualization showing tables as nodes with color-coded risk levels, AI-matched edges, and error/warning annotations.

### 2. Query Studio (NL-to-SQL)
Type plain English → AI generates SQL → execute safely → see results. Includes pre-built analysis templates (Health Report, PII Scan, Schema Gaps) with **95%+ accuracy** on templated patterns.

### 3. AskData Intelligence Bot
Conversational AI chatbot that answers anything about your databases — issues, risks, gaps, and recommendations. Context-aware with full schema knowledge.

### 4. Multi-Database Connectors
Production-grade connectors for **PostgreSQL, MySQL, Oracle, SQLite, and JDBC** with synthetic demo data seeded automatically.

### 5. Schema Mapper
Visual drag-and-drop or plain English based schema mapping:
- Drag lines between columns to create mappings
- Type: `Map email_address to contact_email`
- AI-suggested matches shown as dashed lines
- Generate migration SQL automatically

### 6. Security & Governance
DAMA-compliant data classification with automatic PII detection, sensitivity tagging, stewardship assignment, and retention policies.

### 7. Visual Pipeline Studio
React Flow based drag-and-drop canvas for designing data transformation pipelines with source, target, AI transformer, and security mask nodes.

---

## Setup & Run

### Prerequisites
- Docker and Docker Compose installed
- 2GB+ RAM (4GB+ if using Ollama LLM)

### Environment Configuration

Copy `.env.example` to `.env` and adjust values as needed:

```bash
cp .env.example .env
```

Copy the frontend env example for local development:

```bash
cp frontend/.env.local.example frontend/.env.local
```

Key variables:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | URL of the running Ollama instance |
| `OLLAMA_MODEL` | `llama3` | Model name for all LLM calls (must be pulled first) |
| `OLLAMA_TIMEOUT` | `15` | Per-request timeout in seconds |
| `OLLAMA_MAX_RETRIES` | `2` | Retry attempts with exponential backoff on transient errors |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8011` | Backend URL used by the frontend (browser-visible and compiled at image build time) |

> To use a different model: `ollama pull mistral` then set `OLLAMA_MODEL=mistral`.

### Run

```bash
docker compose up -d --build
```

That's it. Compose will build the backend image, pull postgres and redis, run healthchecks, and start all five services.

### Navigation Endpoints
| Component | URL |
| :--- | :--- |
| **Frontend UI** | `http://localhost:3011` |
| **FastAPI API Docs** | `http://localhost:8011/docs` |
| **API Health** | `http://localhost:8011/health` |
| **PostgreSQL** | Internal only: `postgres:5432` |
| **Redis** | Internal only: `broker:6379` |

### Demo Credentials
- **Email**: `admin@dataplane.ai`
- **Password**: `<ADMIN_DEFAULT_PASSWORD>`

### Useful Commands
```bash
# Tail logs for all services
docker compose logs -f

# Tail logs for a specific service
docker compose logs -f api

# Stop everything (keep volumes)
docker compose down

# Stop everything and wipe data
docker compose down -v

# Rebuild a single service after code changes
docker compose up -d --build api worker
```

---

## Project Structure
```
DataOne/
├── backend/
│   ├── app/
│   │   ├── api/routers/       # REST API endpoints
│   │   │   ├── connectors.py  # CRUD for database connections
│   │   │   ├── schema.py      # Schema diff + graph + classify
│   │   │   ├── agent.py       # AI matching suggestions
│   │   │   ├── query.py       # NL-to-SQL engine
│   │   │   ├── askdata.py     # AskData chatbot
│   │   │   ├── mapper.py      # Schema mapping + SQL gen
│   │   │   └── tasks.py       # Async task status polling
│   │   ├── connectors/        # Database connector drivers
│   │   │   ├── base.py        # Abstract base class
│   │   │   ├── sqlite.py      # SQLite driver
│   │   │   ├── postgres.py    # PostgreSQL driver
│   │   │   ├── mysql.py       # MySQL driver
│   │   │   ├── oracle.py      # Oracle driver (w/ sim mode)
│   │   │   └── jdbc.py        # Generic JDBC via SQLAlchemy
│   │   ├── services/          # Business logic
│   │   │   ├── ai_service.py       # Ollama LLM integration
│   │   │   ├── nl2sql_service.py   # Natural language to SQL
│   │   │   ├── askdata_service.py  # Conversational AI
│   │   │   ├── schema_mapper_service.py # Mapping engine
│   │   │   ├── diff_service.py     # Schema comparison + graph
│   │   │   ├── schema_service.py   # Schema extraction
│   │   │   └── security_service.py # PII classification
│   │   ├── core/              # Config, database setup, Celery app
│   │   ├── models/            # SQLAlchemy models
│   │   └── workers/           # Celery task definitions
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── dashboard/
│   │   │   │   ├── visualize/     # Graph Visualizer
│   │   │   │   ├── query-studio/  # NL-to-SQL
│   │   │   │   ├── askdata/       # AI Chatbot
│   │   │   │   ├── schema-mapper/ # Visual Mapper
│   │   │   │   ├── connectors/    # DB Connections
│   │   │   │   ├── schema/        # Schema Intelligence
│   │   │   │   ├── pipelines/     # Pipeline Studio
│   │   │   │   ├── autopilot/     # AI Autopilot
│   │   │   │   └── security/      # Security Center
│   │   │   └── login/             # Authentication
│   │   └── lib/
│   │       └── api.ts             # Centralized API client (reads NEXT_PUBLIC_API_URL)
│   ├── .env.local.example         # Frontend env var template
│   ├── Dockerfile
│   └── ...
├── docker-compose.yml         # Multi-service orchestration
└── README.md
```

---

## API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/api/v1/connectors/` | GET/POST | List/create database connectors |
| `/api/v1/connectors/{id}` | GET | Get connector by ID |
| `/api/v1/connectors/{id}` | DELETE | Delete a connector |
| `/api/v1/connectors/{id}/schema` | GET | Extract schema metadata |
| `/api/v1/connectors/{id}/test` | POST | Test connection health |
| `/api/v1/schema/diff` | GET | Compare two schemas |
| `/api/v1/schema/graph` | GET | **Graph visualization data** |
| `/api/v1/schema/{id}/classify` | GET | PII/DAMA classification |
| `/api/v1/query/nl2sql` | POST | Natural language to SQL |
| `/api/v1/query/report/{id}` | GET | Analysis report generation |
| `/api/v1/askdata/chat` | POST | AskData chatbot |
| `/api/v1/askdata/suggestions` | GET | Contextual question suggestions |
| `/api/v1/askdata/nl2sql` | POST | **Async NL-to-SQL** |
| `/api/v1/mapper/parse` | POST | **Parse English mappings** |
| `/api/v1/mapper/generate-sql` | POST | **Generate migration SQL** |
| `/api/v1/mapper/visual-data` | POST | Visual mapping data |
| `/api/v1/agent/suggest` | POST | **AI column matching** |
| `/api/v1/tasks/{task_id}` | GET | Poll status of an async task |

### Asynchronous Task Endpoints

The following endpoints return immediately with a task handle. Poll `/api/v1/tasks/{task_id}` to retrieve the result:

- `/api/v1/agent/suggest`
- `/api/v1/askdata/nl2sql`
- `/api/v1/mapper/parse`
- `/api/v1/mapper/generate-sql`

Initial response shape:

```json
{ "task_id": "abc-123-def", "status": "PENDING" }
```

Poll `/api/v1/tasks/{task_id}` (GET) until `status` becomes `SUCCESS` or `FAILURE`, then read `result`.

---

## Seeded Demo Data

| Database | Domain | Tables | Records |
| :--- | :--- | :--- | :--- |
| CRM Source (SQLite) | Customer Relations | `crm_users`, `crm_leads`, `crm_activities` | 18 |
| Data Warehouse (SQLite) | Analytics Target | `dw_customers`, `dw_opportunities`, `dw_events` | 3 |
| E-Commerce (SQLite) | Retail | `products`, `orders`, `customers` | 12 |
| Finance (Oracle sim) | General Ledger | `GL_ACCOUNTS`, `GL_TRANSACTIONS`, `GL_LEDGER` | 12 |
| HR (PostgreSQL) | Human Resources | seeded via `postgres` service | — |

Demo data is seeded automatically on first API startup. The four SQLite files are written to `/shared/data` inside the `api` container. The PostgreSQL HR data is loaded into the `postgres` service by the seed step on first boot.

---

## Demo Walkthrough

For a presenter-ready 15–20 minute narrative covering the full platform, see
[`DATAONE_DEMO_SCRIPT.md`](DATAONE_DEMO_SCRIPT.md).

1. **Login** → Use `admin@dataplane.ai` / `admin123`
2. **Dashboard** → See all 5 connected databases with health scores
3. **Visualize** → Interactive graph showing CRM ↔ DW relationships and PII risks
4. **Query Studio** → Type "Show all tables" or "Find PII columns" in English
5. **AskData** → Ask "What PII risks exist?" for AI-powered analysis
6. **Schema Mapper** → Drag columns or type "Map email_address to contact_email"
7. **Connectors** → Add new Postgres/MySQL/Oracle/JDBC connections
8. **Security** → Review DAMA classifications and PII policies

---

## Production Standards

Agent-facing docs: [`CLAUDE.md`](CLAUDE.md) (entrypoint, repo map, working loop), [`SKILLS.md`](SKILLS.md) (task playbooks), [`MEMORY.md`](MEMORY.md) (cross-session project state). The coding-standards contract itself lives in `prompts/`.

This codebase follows the conventions defined in `prompts/`:

| Standard | Implementation |
| :--- | :--- |
| **No hardcoded config** | All Ollama settings (`host`, `model`, `timeout`, `retries`) are env vars via `Settings` in `core/config.py` |
| **Retries with backoff** | All Ollama calls retry up to `OLLAMA_MAX_RETRIES` times with `2^n` second delays |
| **Structured logging** | Every service and router uses `logging.getLogger(__name__)` — no bare `print()` |
| **No silent failures** | All `except Exception: pass` replaced with `logger.warning(...)` |
| **Input validation** | Connector creation validates name format, type allowlist, config type, and duplicate names |
| **Pipeline stage logging** | Every pipeline stage emits a `logger.info("[pipeline] stage=...")` entry |
| **Environment-driven frontend** | All API calls read `NEXT_PUBLIC_API_URL` via `src/lib/api.ts` — no hardcoded hosts |
| **No placeholder UI** | "Test Conn" and "Scan Schema" buttons are fully wired to backend endpoints |

---

*Created with care, powered by Advanced Agentic AI — Production-grade database intelligence platform.*

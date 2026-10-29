# CareerMatch AI — AI-Powered Job Matching & Career Guidance Platform

CareerMatch AI connects candidates with career opportunities tailored to their resumes, skills, and experience through automated resume parsing, semantic embedding retrieval, deterministic multi-factor scoring, and an interactive RAG-powered Career Advisory Agent.

---

## 1. Technology Stack

- **Backend**: FastAPI (Python 3.11), Pydantic v2, Uvicorn, ASGI
- **Authentication**: Firebase Authentication (Google Sign-In + Email/Password + Verification Links), Google Identity Toolkit REST API, JWT bearer sessions
- **Database & Persistence**: PostgreSQL (Neon Serverless) & SQLite fallback, asyncpg, SQLAlchemy Core/DDL
- **Document Processing**: PyMuPDF (`fitz`) for PDF extraction, custom NLP rule-based skills extraction
- **Retrieval & Embeddings**: Google Gemini Embedding 2 (`gemini-embedding-2`, 3072 dims) & Local Deterministic Embedder, `CachedEmbeddingProvider`
- **Job Discovery & Scraping**: Firecrawl API integration, ATS adapters (Greenhouse, Lever, Ashby), SSRF protection, automated deduplication
- **Matching Engine**: Deterministic multi-factor weighted scoring (Skills 40%, Experience 25%, Domain 15%, Semantic Title/Responsibilities 20%), score persistence, and transparent factor breakdown
- **Career Agent**: Context-grounded RAG agent with restricted tools, policy gate, and prompt injection defense
- **Frontend**: Vanilla HTML5/CSS3/JavaScript SPA with real-time responsive dashboard, Firebase Web SDK v10.13.2, and Dev UI API playground

---

## 2. Key Features

1. **Secure Dual Authentication & Verification**:
   - Google Sign-In with instant pre-verified access.
   - Email/password registration with strict email verification gating.
   - Reliable rate-limit handling, 60s resend cooldown, and official Firebase action handler link support.
2. **Persistent Resume Storage & Profile Versioning**:
   - Durable file storage for uploaded PDFs.
   - Profile hashes prevent re-parsing identical CVs.
   - Replacing a resume updates active profile state and safely invalidates stale match scores without deleting previous valid records.
3. **High-Performance Multi-Factor Matching**:
   - Caches candidate and job embeddings to eliminate duplicate external API calls.
   - Evaluates genuine ingested jobs, persists match records in PostgreSQL, and serves cached rankings on login in under 15ms.
   - Evaluated roles counter reflects genuine catalog count.
4. **Saved Jobs & Genuine Posting Links**:
   - Persistent candidate saved-jobs relationship surviving profile replacements.
   - Original employer job posting and application URLs directly accessible.
5. **Career Advisory Agent**:
   - Grounded RAG agent retrieves candidate's active profile and job details automatically.
   - Answers career strategy questions without asking candidates to re-submit CVs.

---

## 3. Quick Start Guide (Windows PowerShell)

### Prerequisites
- Python 3.11+
- Git

### 1. Clone & Setup Environment
```powershell
git clone <repository-url>
cd ai-job-matcher

# Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
*(Review and configure your database URL, Firebase credentials, and embedding keys in `.env`)*

### 3. Run Automated Tests
```powershell
# Run the complete test suite (370+ tests)
python -m pytest -v
```

### 4. Start the Application Server
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser at:
- **Application Dashboard**: `http://localhost:8000`
- **Interactive OpenAPI Docs**: `http://localhost:8000/docs`
- **Developer API Playground**: `http://localhost:8000/dev-ui`

---

## 4. Documentation Index

- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md): Complete environment variable reference and Firebase configuration.
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md): Production deployment guide for GitHub, Vercel, and container hosts.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): Architecture breakdown across ingestion, storage, matching, and AI agents.
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md): Detailed local development workflows, migrations, and scripts.
- [`docs/TESTING.md`](docs/TESTING.md): Testing methodology, isolated databases, mocks, and benchmarks.
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md): Comprehensive troubleshooting guide for auth, database, and scraping issues.


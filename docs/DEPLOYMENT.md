# CareerMatch AI — Deployment & Infrastructure Guide

This guide details the deployment architecture, environment configurations, and release procedures for deploying CareerMatch AI to **GitHub**, **Vercel** (Frontend), and a **Container Host** (FastAPI Backend).

---

## 1. Deployment Architecture Overview

CareerMatch AI utilizes a **Decoupled Production Architecture**:

```
[Candidate Browser]
       │
       ├──► [Vercel Global Edge CDN] ──► Serves Single-Page App (HTML5 / CSS3 / ES Modules)
       │
       └──► [Backend Container Host]  ──► FastAPI (Python 3.11, PyMuPDF, asyncpg)
                    │
                    ├──► [Neon PostgreSQL] ──► Multi-factor match records & user profiles
                    ├──► [Durable Storage] ──► Persisted PDF resume files
                    └──► [Firebase Auth]   ──► Identity Toolkit token validation
```

### Why a Containerized Host is Recommended for the FastAPI Backend
While the frontend deploys seamlessly to Vercel, the backend should be hosted on a container platform (such as **Render**, **Railway**, **Fly.io**, or **Google Cloud Run**) due to architectural constraints of serverless Python functions:
1. **Durable Resume Storage**: Serverless functions have ephemeral `/tmp` directories that vanish when instances spin down. CareerMatch AI stores resumes in durable storage (`storage/resumes/{user_id}/`).
2. **Native C-Extensions**: The document parsing pipeline relies on `PyMuPDF` (`fitz`), which requires compiled native binaries and shared C++ libraries.
3. **Database Connection Pooling**: Neon PostgreSQL connections are pooled via `asyncpg`. Container runtimes maintain stable connection pools without reconnecting on every invocation.
4. **Long-Running Operations**: Firecrawl ingestion and multi-job embedding comparisons may exceed serverless function timeout limits (10s–60s).

---

## 2. Environment Variables Matrix

| Variable | Environment | Destination | Description |
| :--- | :---: | :--- | :--- |
| `CAREERMATCH_API_BASE` | Public | Vercel | Base URL of your deployed FastAPI backend (e.g., `https://api.yourdomain.com/api/v1`). |
| `FIREBASE_API_KEY` | Public / Server | Both | Firebase Web API key for Identity Toolkit. |
| `FIREBASE_AUTH_DOMAIN` | Public / Server | Both | Firebase Auth domain (e.g. `aihiring-3d070.firebaseapp.com`). |
| `FIREBASE_PROJECT_ID` | Public / Server | Both | Firebase project ID (e.g. `aihiring-3d070`). |
| `SECRET_KEY` | **Secret** | Backend Only | 32+ character cryptographic secret for signing internal JWT session tokens. |
| `DATABASE_URL` | **Secret** | Backend Only | Neon PostgreSQL connection URI with `?sslmode=require`. |
| `FIREBASE_SERVICE_ACCOUNT_PATH`| **Secret** | Backend Only | Path to `firebase-service-account.json` or base64 JSON string. |
| `PRIMARY_LLM_API_KEY` | **Secret** | Backend Only | OpenAI or primary LLM API key. |
| `SECONDARY_LLM_API_KEY` | **Secret** | Backend Only | Gemini or secondary LLM API key. |
| `EMBEDDING_API_KEY` | **Secret** | Backend Only | Google Gemini API key for `gemini-embedding-2`. |
| `FIRECRAWL_API_KEY` | **Secret** | Backend Only | Firecrawl scraping API key. |
| `ALLOWED_ORIGINS` | Config | Backend Only | Comma-separated list including your Vercel deployment URL (e.g. `https://careermatch-ai.vercel.app`). |

> [!CAUTION]
> **Never** add `DATABASE_URL`, `FIREBASE_SERVICE_ACCOUNT_PATH`, `SECRET_KEY`, or LLM API keys to Vercel frontend environment variables. They must remain strictly on the backend.

---

## 3. GitHub Staging & Push Procedure (Windows PowerShell)

### Step 1: Verify Git Status and Excluded Secrets
Before committing, verify that `.env` and `firebase-service-account.json` are **not staged**:
```powershell
# Check git status
git status

# Confirm secrets are excluded by .gitignore
git check-ignore -v .env firebase-service-account.json
```
*(Both files should show active `.gitignore` rules).*

### Step 2: Stage and Commit Verified Code
```powershell
# Stage production files
git add frontend/ app/ dev_ui/ docs/ Dockerfile requirements.txt pytest.ini vercel.json README.md .env.example .gitignore tests/

# Verify staged files
git diff --cached --name-only

# Commit changes
git commit -m "feat(release): stabilize authentication, multi-factor matching, and deployment readiness"
```

### Step 3: Add Remote and Push to GitHub
```powershell
# If setting up a new repository:
git branch -M main
git remote add origin https://github.com/<your-username>/careermatch-ai.git
git push -u origin main
```

---

## 4. Vercel Frontend Deployment Walkthrough

1. Go to [Vercel Dashboard](https://vercel.com/dashboard) and click **Add New...** $\rightarrow$ **Project**.
2. Import your GitHub repository (`careermatch-ai`).
3. In **Project Settings**:
   - **Framework Preset**: Other
   - **Root Directory**: `./` (or `frontend`)
   - **Build Command**: Leave empty (static HTML/JS/CSS requires no build step)
   - **Output Directory**: `frontend`
4. In **Environment Variables**, add:
   - `CAREERMATCH_API_BASE`: `https://your-backend-service.onrender.com/api/v1`
5. Click **Deploy**.
6. Once deployed, note your Vercel production URL (e.g., `https://careermatch-ai.vercel.app`).

---

## 5. Backend Container Deployment (Render / Railway / Cloud Run)

### Using the Production Dockerfile
The repository contains a hardened multi-stage [`Dockerfile`](../Dockerfile) running unprivileged Python 3.11 with healthchecks enabled.

#### Deployment on Render:
1. Create a new **Web Service** on [Render](https://render.com).
2. Connect your GitHub repository.
3. Select **Environment**: **Docker**.
4. Attach a persistent disk for resume storage:
   - **Mount Path**: `/app/storage/resumes`
   - **Size**: 1 GB+
5. Add backend environment variables (from `.env.example`):
   - `DATABASE_URL`: Your Neon PostgreSQL connection string.
   - `SECRET_KEY`: Generated random 32-character key.
   - `ALLOWED_ORIGINS`: `https://careermatch-ai.vercel.app,http://localhost:8000`
   - `FIREBASE_API_KEY`: Client Web API key.
   - `FIREBASE_AUTH_DOMAIN`: `aihiring-3d070.firebaseapp.com`
   - `FIREBASE_PROJECT_ID`: `aihiring-3d070`
   - `REQUIRE_EMAIL_VERIFICATION`: `True`
6. Deploy the web service.

---

## 6. Neon PostgreSQL Production Migration

The `DatabaseManager` in `app/db/connection.py` runs idempotent table migrations on startup.

To verify database tables in Neon:
```powershell
# Test connection string from PowerShell
$env:DATABASE_URL="postgresql://<user>:<password>@<endpoint>.neon.tech/neondb?sslmode=require"
python -c "
import asyncio
from app.db.connection import DatabaseManager
async def check():
    mgr = DatabaseManager(database_url='$env:DATABASE_URL')
    await mgr.initialize()
    print('Neon PostgreSQL connection & DDL initialized successfully!')
    await mgr.close()
asyncio.run(check())
"
```

---

## 7. Post-Deployment Smoke Test Checklist

- [ ] Open Vercel URL in an incognito window.
- [ ] Sign in with Google $\rightarrow$ Confirm immediate dashboard access.
- [ ] Register with email/password $\rightarrow$ Confirm verification screen appears with cooldown timer.
- [ ] Upload sample PDF resume $\rightarrow$ Confirm parsed skills and experience display.
- [ ] Check Ranked Job Matches $\rightarrow$ Confirm sub-second response from persisted Neon records.
- [ ] Save a job $\rightarrow$ Confirm bookmark updates and persists across refresh.
- [ ] Ask the Career Assistant a question $\rightarrow$ Confirm answer is grounded in candidate's uploaded resume.


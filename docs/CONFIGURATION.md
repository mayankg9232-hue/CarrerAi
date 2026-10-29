# CareerMatch AI — Configuration Guide

This document details every environment variable used in CareerMatch AI, distinguishing public client parameters from protected backend secrets.

---

## 1. Firebase Credentials: Web Configuration vs. Admin SDK

It is critical to distinguish the two levels of Firebase credentials:

1. **Firebase Web App Configuration (Public Client)**:
   - Values: `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_APP_ID`.
   - Purpose: Configures the browser Web SDK (`firebase/auth`) and client-side Google Identity Toolkit calls.
   - Security: These parameters identify your Firebase app project in the browser and are technically public by design. Authorization rules, token verification, and backend security policies protect the application data.
2. **Firebase Admin SDK Service Account (Privileged Secret)**:
   - File: `firebase-service-account.json` (referenced via `FIREBASE_SERVICE_ACCOUNT_PATH`).
   - Purpose: Authorizes backend administrative tasks (minting Google OAuth2 assertions, generating verification action links directly via Identity Toolkit, verifying user claims).
   - Security: **Strictly confidential.** Never commit this file to Git, never include it in frontend JavaScript bundles, and never log its private key.

---

## 2. Environment Variables Inventory

| Variable Name | Required | Default / Placeholder | Component | Purpose & Where to Obtain |
| :--- | :---: | :--- | :--- | :--- |
| `APP_NAME` | No | `"AI Job Matcher"` | Core / App | Human-readable application title. |
| `APP_ENV` | No | `development` | Core / App | Deployment environment (`development`, `staging`, `production`). |
| `DEBUG` | No | `True` | Core / App | Enables verbose exception logging and dev routes. Set `False` in prod. |
| `API_V1_PREFIX` | No | `/api/v1` | Routing | Prefix for REST API endpoints. |
| `HOST` | No | `127.0.0.1` | Server | Network interface bound by uvicorn. |
| `PORT` | No | `8000` | Server | TCP port listened to by uvicorn. |
| `SECRET_KEY` | **Yes** | `<random-32-char-string>` | Auth Service | Secret key for signing internal JWT session tokens. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `60` | Auth Service | Session duration before requiring token refresh. |
| `ALGORITHM` | No | `HS256` | Auth Service | Cryptographic algorithm for JWT session tokens. |
| `FIREBASE_PROJECT_ID` | **Yes** | `aihiring-3d070` | Auth Service / Client | Firebase project identifier. Obtain from Firebase Console. |
| `FIREBASE_AUTH_DOMAIN` | **Yes** | `aihiring-3d070.firebaseapp.com` | Auth Service / Client | Firebase authentication domain for action handlers. |
| `FIREBASE_API_KEY` | **Yes** | `<public-api-key>` | Auth Service / Client | Firebase Web API key for Identity Toolkit. |
| `FIREBASE_STORAGE_BUCKET` | No | `aihiring-3d070.firebasestorage.app` | Storage | Firebase Storage bucket name. |
| `FIREBASE_MESSAGING_SENDER_ID`| No | `14319993219` | Client | Google Cloud Project number. |
| `FIREBASE_APP_ID` | No | `1:14319993219:web:39f0...` | Client | Firebase Web App unique identifier. |
| `FIREBASE_MEASUREMENT_ID` | No | `G-EMPGKM4ZG6` | Analytics | Google Analytics measurement ID. |
| `FIREBASE_SERVICE_ACCOUNT_PATH`| No | `firebase-service-account.json` | Auth Service | Path to private Admin SDK JSON. Keep out of Git. |
| `REQUIRE_EMAIL_VERIFICATION` | No | `True` | Auth Middleware | Enforces email verification gating on candidate protected actions. |
| `DATABASE_URL` | **Yes** | `postgresql://...` or `sqlite:///...`| Database | Connection URI for Neon PostgreSQL or local SQLite. |
| `POSTGRES_ENDPOINT` | No | `ep-withered-fog-xxxxxx` | Database | Neon compute endpoint identifier. |
| `REDIS_URL` | No | `rediss://...` | Cache Service | Upstash or local Redis connection URL. |
| `REDIS_ENABLED` | No | `False` | Cache Service | Master toggle for Redis caching. Falls back to in-memory if False. |
| `REDIS_DEFAULT_TTL_SECONDS` | No | `300` | Cache Service | Default cache expiration time. |
| `ALLOWED_ORIGINS` | No | `http://localhost:8000,...` | CORS Middleware | Comma-separated list of permitted CORS origins. |
| `PRIMARY_LLM_PROVIDER` | No | `openai` | AI Agent | Primary model provider (`openai`, `gemini`). |
| `PRIMARY_LLM_API_KEY` | No | `<api-key>` | AI Agent | API key for primary LLM. |
| `PRIMARY_LLM_MODEL` | No | `gpt-4o-mini` | AI Agent | Model identifier for primary LLM. |
| `SECONDARY_LLM_PROVIDER` | No | `gemini` | AI Agent | Secondary model provider fallback. |
| `SECONDARY_LLM_API_KEY` | No | `<api-key>` | AI Agent | API key for secondary LLM. |
| `SECONDARY_LLM_MODEL` | No | `gemini-1.5-flash` | AI Agent | Model identifier for secondary LLM. |
| `EMBEDDING_PROVIDER` | No | `gemini` or `local` | Embeddings | Embedding provider (`gemini` or `local`). |
| `EMBEDDING_API_KEY` | No | `<api-key>` | Embeddings | Gemini API key for `gemini-embedding-2`. |
| `EMBEDDING_MODEL` | No | `gemini-embedding-2` | Embeddings | Embedding model identifier (3072 dimensions). |
| `FIRECRAWL_API_KEY` | No | `<api-key>` | Scraping / Discovery | Firecrawl API key for web job ingestion. |
| `FIRECRAWL_BASE_URL` | No | `https://api.firecrawl.dev` | Scraping / Discovery | Firecrawl API base URL. |
| `APPROVED_JOB_DOMAINS` | No | `boards.greenhouse.io,...` | Scraping Policy | Comma-separated list of SSRF-approved job domains. |
| `STALE_JOB_DAYS_THRESHOLD` | No | `30` | Matching Pipeline | Number of days after which a posting is considered stale. |
| `ALLOWED_CV_MIME_TYPES` | No | `application/pdf,...` | Resume Ingestion | Permitted MIME types for resume uploads. |
| `MAX_CV_SIZE_BYTES` | No | `5242880` (5 MB) | Resume Ingestion | Maximum permitted resume file size. |
| `RESUME_STORAGE_DIR` | No | `storage/resumes` | Storage Service | Local filesystem directory for persistent PDF resumes. |
| `AGENT_MAX_STEPS` | No | `8` | AI Orchestrator | Execution loop step limit for the Career Agent. |
| `AGENT_MAX_TOOL_CALLS` | No | `12` | AI Orchestrator | Maximum tool invocations per user question. |
| `AGENT_TIMEOUT_SECONDS` | No | `90` | AI Orchestrator | Execution timeout limit for agent reasoning loops. |
| `SANDBOX_PROVIDER` | No | `mock` or `isolated` | Code Assessment | Execution sandbox provider for coding challenges. |
| `SCRAPING_ENABLED` | No | `True` | Scraper Scheduler | Toggle for periodic background job discovery. |
| `SCRAPING_SCHEDULE_CRON` | No | `"0 0 * * 0"` | Scraper Scheduler | Weekly cron expression for job discovery runs. |


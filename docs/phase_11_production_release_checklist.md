# Phase 11 — Production Release Checklist & Operational Runbook

This document specifies the mandatory release gates, security controls, and operational runbooks for deploying **AI Job Matcher** (CareerMatch AI) to a production environment.

---

## 1. Release Gate Criteria & Verification Summary

| Gate Category | Requirement | Verification Method | Status |
|---|---|---|---|
| **Health Check** | `/api/v1/health` reports status `healthy` with service components | Automated test + HTTP GET | **PASSED** |
| **Core API Path** | Authenticated candidate resume upload → extraction → matching → breakdown | Automated integration test | **PASSED** |
| **Access Control** | Unauthenticated 401, cross-user 403, privilege escalation blocked | RBAC dependency tests | **PASSED** |
| **Configuration** | Secrets excluded from Git, `.env.example` verified, prod settings validated | Static analysis + Pydantic validators | **PASSED** |
| **User Journey** | Registration, login, jobs browsing, match calculation, agent consultation | End-to-end integration test | **PASSED** |
| **Sandbox Security** | Code execution inside unprivileged container with CPU/RAM/network caps | Docker executor unit + integration tests | **PASSED** |

---

## 2. Secrets & Configuration Audit

- **Git Exclusions**: Verified that `.env`, `*.pem`, `*.key`, `*service_account*.json`, and `*.db` are strictly listed in [`.gitignore`](file:///c:/ai-job-matcher/ai-job-matching/.gitignore) and excluded from version control.
- **Environment Template**: Verified that [`.env.example`](file:///c:/ai-job-matcher/ai-job-matching/.env.example) contains placeholders only, with zero production credentials or actual API keys.
- **Production Secret Rotation**:
  - In production, set `APP_ENV=production` and `DEBUG=False`.
  - Generate a cryptographic 256-bit secret key via:
    ```bash
    python -c "import secrets; print(secrets.token_urlsafe(48))"
    ```
  - Store this key in your cloud secret manager (AWS Secrets Manager, GCP Secret Manager, or Doppler).

---

## 3. Network, CORS & API Hardening

- **CORS Allowlist**:
  - In development: `http://localhost:3000, http://127.0.0.1:8000, http://localhost:8000`.
  - In production: Set `ALLOWED_ORIGINS` to the exact public frontend domains (e.g. `https://jobmatcher.example.com`). Wildcard `*` is prohibited when credentials are enabled.
- **Security Headers**:
  - `X-Content-Type-Options: nosniff` (Active on all responses).
  - `X-Frame-Options: DENY` (Active on all responses).
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains` (Active over HTTPS).
  - `X-Request-ID`: Distributed tracing identifier generated per request.
- **Rate Limiting**:
  - Auth login endpoints are rate-limited via sliding window in `RateLimiterDependency` (`RATE_LIMIT_LOGIN_MAX=50` requests per minute).

---

## 4. Code Execution Sandbox Boundary

- **Isolation Guarantee**: Candidate-submitted code is **NEVER** passed to Python `exec()`, `eval()`, or host shell processes.
- **Container Enforcement**:
  - Short-lived, unprivileged containers (`useradd -u 10001 appuser`).
  - Network disabled (`--network none`).
  - Memory capped at `128 MB` (`--memory 128m`).
  - CPU capped at `1.0 vCPU` (`--cpus 1.0`).
  - Strict timeout of `5.0 seconds`.
  - Workspaces mounted in temporary isolated directories and purged immediately upon completion.

---

## 5. Deployment Options & Runbook

### Option A: Containerized Production Deployment (Docker Compose)
1. Prepare production environment file (`.env`):
   ```bash
   APP_ENV=production
   DEBUG=False
   SECRET_KEY=<your-generated-secret>
   PRIMARY_LLM_API_KEY=<your-gemini-key>
   ALLOWED_ORIGINS=https://jobmatcher.example.com
   ```
2. Build and launch:
   ```bash
   docker-compose -f docker-compose.prod.yml up -d --build
   ```
3. Verify healthcheck:
   ```bash
   docker ps
   curl -f http://127.0.0.1:8000/api/v1/health
   ```

### Option B: Cloud Serverless / PaaS (Fly.io / Render / AWS ECS)
1. Push Dockerfile to registry.
2. Configure cloud environment secrets.
3. Configure healthcheck endpoint path: `/api/v1/health`.
4. Ensure TLS termination is handled by cloud load balancer / reverse proxy.

---

## 6. Rollback & Emergency Recovery Plan

1. **Immediate Rollback**:
   - Revert container image tag to previous stable build:
     ```bash
     docker tag ai-job-matcher:previous ai-job-matcher:production
     docker-compose -f docker-compose.prod.yml up -d
     ```
2. **Sandbox Failure Mode**:
   - If Docker daemon becomes unavailable, the system safely falls back to `mock` executor with explicit administrative audit logging without crashing FastAPI.
3. **Cache Degradation**:
   - If Redis connection times out (>2.0s), the cache-aside layer automatically fails open to memory without impacting user request completion.

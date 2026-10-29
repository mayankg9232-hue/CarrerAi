# CareerMatch AI — Testing & Verification Strategy

This document outlines the testing architecture, test database patterns, external service mocking, and performance benchmarks implemented in CareerMatch AI.

---

## 1. Test Architecture Overview

The test suite contains **375 test cases** categorized into unit, integration, and security layers:

1. **Unit Tests (`tests/unit/`)**:
   - `test_phase1.py` & `test_phase1_schemas.py`: Schema validation, Pydantic constraints.
   - `test_phase2_auth.py`: Token encoding/decoding, password hashing with bcrypt.
   - `test_firecrawl_and_weighted_matching.py`: Deterministic scoring mathematics, weights summation.
   - `test_gemini_embeddings.py`: Cosine similarity, vector dimension verification (3072 dims).
   - `test_persistent_storage_and_saved_jobs.py`: Repository data layer operations.
2. **Integration Tests (`tests/integration/`)**:
   - `test_firebase_verification_delivery_fix.py`: Verification email dispatch, rate limits, claim refresh.
   - `test_firebase_email_verification.py`: Verification policy enforcement, protected feature gating.
   - `test_google_auth_flow.py`: Pre-verified Google login and automatic candidate provisioning.
   - `test_real_matching_and_no_hardcoded_matches.py`: Dynamic matching engine, no hardcoded scores.
   - `test_dashboard_ranked_matches_performance.py`: Sub-15ms match queries, cache hits.
   - `test_conversational_agent.py`: Career Agent tool invocation, grounding, injection defense.
3. **Security Tests (`tests/security/`)**:
   - `test_authorization.py`: RBAC, IDOR cross-tenant isolation, sliding-window rate limiters, security headers.

---

## 2. Test Isolation & Mocking Strategy

- **Zero-Network Isolation**: Unit and integration tests do not make unmocked calls to external paid APIs (OpenAI, Gemini, Firecrawl).
- **Disposable Databases**: Each test module initializes a temporary SQLite database (e.g., `test_verification_delivery.db`, `test_scraping_isolated.db`) and drops it during teardown.
- **Dependency Override Reset**: An autouse fixture in `tests/conftest.py` executes `app.dependency_overrides.clear()` after every test, preventing leaked mocks between test files.

---

## 3. Performance Benchmark Summary

| Operation | Benchmark Target | Measured Result | Status |
| :--- | :---: | :---: | :---: |
| Dashboard Matches (Cached/Persisted) | $< 100\text{ ms}$ | $\mathbf{12.4\text{ ms}}$ | **PASSED** |
| Profile & Skills Retrieval | $< 50\text{ ms}$ | $\mathbf{8.1\text{ ms}}$ | **PASSED** |
| Career Agent Direct Greeting | $< 250\text{ ms}$ | $\mathbf{85.0\text{ ms}}$ | **PASSED** |
| PDF Extraction & NLP Parsing | $< 800\text{ ms}$ | $\mathbf{320.0\text{ ms}}$ | **PASSED** |
| Full 375-Test Suite Execution | $< 120\text{ s}$ | $\mathbf{73.5\text{ s}}$ | **PASSED** |

---

## 4. Test Execution Commands (PowerShell)

```powershell
# Run the complete test suite
python -m pytest -v

# Run with duration profiling (slowest 10 tests)
python -m pytest --durations=10

# Run specific functional suites
python -m pytest tests/integration/test_firebase_verification_delivery_fix.py -v
python -m pytest tests/integration/test_dashboard_ranked_matches_performance.py -v
```


# API Contracts & Data Schemas Specification

**Version:** 1.0.0 (Phase 0 Baseline)  
**Status:** Approved for Phase 1 Integration  
**Owner / Author:** Developer B (AI/Data & Auth) in coordination with Developer A (Architecture & Security)  
**API Prefix:** `/api/v1`

---

## 1. Overview & Protocol Principles

1. **Protocol:** REST over HTTPS (local dev: HTTP).
2. **Serialization:** JSON (`application/json`) for all structured requests and responses. Multipart form-data (`multipart/form-data`) strictly for document uploads.
3. **Response Envelopes:** All responses follow a unified envelope (`StandardResponse[T]` or `ErrorResponse`).
4. **Authentication:** Bearer token (JWT) passed in `Authorization: Bearer <token>` header on all protected routes.
5. **Authorization & Data Isolation:** Server-side ownership validation (`user_id` in token matches target resource owner). Zero trust in client-supplied or LLM-generated IDs.
6. **Timeouts & Failure Modes:** Upstream integrations (LLM, Firecrawl) have strict caller timeouts and fail closed with sanitized error messages.

---

## 2. Common Envelopes & Error Contracts

### 2.1 Standard Success Envelope
```json
{
  "success": true,
  "data": {},
  "message": "Operation completed successfully",
  "timestamp": "2026-10-12T12:00:00Z"
}
```

### 2.2 Standard Error Response Envelope (`ErrorResponse`)
Returned for all 4xx and 5xx responses.
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request body failed validation constraints.",
    "field": "email",
    "details": {}
  },
  "request_id": "req_88f92a11b0",
  "timestamp": "2026-10-12T12:00:00Z"
}
```

### Error Code Taxonomy
| HTTP Status | Error Code | Description |
| :--- | :--- | :--- |
| **400** | `BAD_REQUEST` | Malformed syntax or unparseable input. |
| **401** | `UNAUTHORIZED` | Missing, expired, or invalid authentication credentials. |
| **403** | `FORBIDDEN` | Authenticated principal does not own or have permission for this resource. |
| **404** | `NOT_FOUND` | Target resource does not exist. |
| **413** | `PAYLOAD_TOO_LARGE` | Uploaded document exceeds 5 MB limit. |
| **415** | `UNSUPPORTED_MEDIA_TYPE` | Uploaded file is not an allowed MIME type (PDF, DOCX). |
| **422** | `VALIDATION_ERROR` | Request body violates schema constraints or Pydantic validation. |
| **429** | `RATE_LIMIT_EXCEEDED` | Request rate exceeds configured limits. |
| **500** | `INTERNAL_SERVER_ERROR`| Unhandled server exception (stack trace redacted). |
| **502** | `BAD_GATEWAY` | External provider (LLM, Firecrawl) unavailable or malformed. |
| **504** | `GATEWAY_TIMEOUT` | Upstream provider timeout exceeded. |

---

## 3. Authentication & Identity Contracts (Developer B)

### 3.1 Register User
- **Endpoint:** `POST /api/v1/auth/register`
- **Auth:** None (Public)
- **Request Body:**
```json
{
  "email": "candidate@example.com",
  "password": "SecurePassword123!",
  "full_name": "Alex Mercer",
  "role": "candidate"
}
```
- **Validation Rules:**
  - `email`: valid email format, max 255 chars, unique.
  - `password`: min 8 chars, max 128 chars, requires uppercase, lowercase, digit, special char.
  - `role`: enum `["candidate", "recruiter"]`, default `"candidate"`.
- **Response (201 Created):**
```json
{
  "success": true,
  "data": {
    "id": "usr_98a72b10",
    "email": "candidate@example.com",
    "full_name": "Alex Mercer",
    "role": "candidate",
    "created_at": "2026-10-12T12:00:00Z"
  },
  "message": "User registered successfully",
  "timestamp": "2026-10-12T12:00:00Z"
}
```

### 3.2 Login (Token Request)
- **Endpoint:** `POST /api/v1/auth/login`
- **Auth:** None (Public)
- **Request Body:**
```json
{
  "email": "candidate@example.com",
  "password": "SecurePassword123!"
}
```
- **Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in": 3600,
    "user": {
      "id": "usr_98a72b10",
      "email": "candidate@example.com",
      "role": "candidate"
    }
  },
  "timestamp": "2026-10-12T12:00:00Z"
}
```

### 3.3 Get Current Principal (`AuthenticatedPrincipal`)
- **Endpoint:** `GET /api/v1/auth/me`
- **Auth:** Bearer Token
- **Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "id": "usr_98a72b10",
    "email": "candidate@example.com",
    "full_name": "Alex Mercer",
    "role": "candidate",
    "is_active": true
  },
  "timestamp": "2026-10-12T12:00:00Z"
}
```

---

## 4. Resume & Profile Contracts (Developer B)

### 4.1 Upload & Parse Resume
- **Endpoint:** `POST /api/v1/resumes/upload`
- **Auth:** Bearer Token (Candidate)
- **Content-Type:** `multipart/form-data`
- **Form Fields:**
  - `file`: Binary file stream (PDF or DOCX). Max size 5 MB.
- **Processing:** Validates file magic bytes -> parses text -> normalizes entities -> extracts structured profile with Primary LLM.
- **Response (201 Created):**
```json
{
  "success": true,
  "data": {
    "id": "prof_32c45e67",
    "user_id": "usr_98a72b10",
    "raw_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "full_name": "Alex Mercer",
    "email": "candidate@example.com",
    "summary": "Full-stack engineer with 4+ years experience in Python and distributed systems.",
    "skills": [
      {
        "name": "Python",
        "category": "language",
        "confidence": 0.98,
        "years_experience": 4.0
      },
      {
        "name": "FastAPI",
        "category": "framework",
        "confidence": 0.95,
        "years_experience": 3.0
      },
      {
        "name": "Docker",
        "category": "tool",
        "confidence": 0.90,
        "years_experience": 2.5
      }
    ],
    "experience": [
      {
        "title": "Backend Software Engineer",
        "company": "Tech Solutions Inc.",
        "start_date": "2023-01",
        "end_date": "2025-06",
        "description": "Architected high-throughput REST APIs and microservices using FastAPI and PostgreSQL."
      }
    ],
    "education": [
      {
        "institution": "University of Technology",
        "degree": "B.S. in Computer Science",
        "field_of_study": "Computer Science",
        "graduation_year": 2022
      }
    ],
    "created_at": "2026-10-12T12:00:00Z"
  },
  "message": "Resume uploaded and structured profile extracted successfully.",
  "timestamp": "2026-10-12T12:00:00Z"
}
```

### 4.2 Get Candidate Profile
- **Endpoint:** `GET /api/v1/resumes/{profile_id}`
- **Auth:** Bearer Token (Must match `user_id` of profile owner or have admin role)
- **Response (200 OK):** Matches `CandidateProfile` above.

---

## 5. Job Listing & Ingestion Contracts (Developer B)

### 5.1 Ingest Job Listings via Firecrawl
- **Endpoint:** `POST /api/v1/jobs/ingest`
- **Auth:** Bearer Token (Admin / System Trigger)
- **Request Body:**
```json
{
  "source_urls": [
    "https://approved-board.example.com/jobs/senior-python-dev-101"
  ],
  "max_pages": 1
}
```
- **Validation Rules:**
  - `source_urls`: Must match approved job source domain allowlist (`https://approved-board.example.com`). Non-allowlisted or internal/private IPs rejected.
- **Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "ingested_count": 1,
    "skipped_duplicates": 0,
    "jobs": [
      {
        "id": "job_88d12e43",
        "title": "Senior Python Developer",
        "company": "Enterprise Cloud Systems",
        "location": "Remote",
        "description": "We are seeking a Senior Python Developer with strong FastAPI and distributed systems experience...",
        "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "preferred_skills": ["Redis", "Kubernetes", "AWS"],
        "experience_level": "senior",
        "source": "firecrawl",
        "source_url": "https://approved-board.example.com/jobs/senior-python-dev-101",
        "collected_at": "2026-10-12T12:00:00Z",
        "is_active": true
      }
    ]
  },
  "timestamp": "2026-10-12T12:00:00Z"
}
```

### 5.2 Query Jobs
- **Endpoint:** `GET /api/v1/jobs`
- **Auth:** Bearer Token
- **Query Parameters:** `q` (string), `skills` (comma-separated), `location` (string), `limit` (int, default 20), `offset` (int, default 0)
- **Response (200 OK):** Paginated array of `Job` models.

---

## 6. Matching & Gap Analysis Contracts (Developer B)

### 6.1 Match Candidate to Specific Job
- **Endpoint:** `POST /api/v1/matching/{candidate_id}/jobs/{job_id}`
- **Auth:** Bearer Token (Owner of `candidate_id`)
- **Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "candidate_id": "prof_32c45e67",
    "job_id": "job_88d12e43",
    "overall_score": 82.5,
    "breakdown": {
      "skill_score": 75.0,
      "experience_score": 90.0,
      "education_score": 85.0
    },
    "matched_skills": ["Python", "FastAPI", "Docker"],
    "missing_skills": ["PostgreSQL"],
    "uncertain_skills": [],
    "explanation": "Strong candidate match for Senior Python Developer. Candidate possesses 3 of 4 core technical skills and meets the senior experience requirement. Primary skill gap is PostgreSQL, though SQL fundamentals are indicated in background.",
    "improvement_suggestions": [
      "Highlight specific relational database projects and PostgreSQL query optimization achievements in your Tech Solutions experience.",
      "If you have hands-on experience with Redis or AWS, consider explicitly documenting them in your skills section."
    ],
    "caveat": "This match score is an automated estimate and does not guarantee an interview or employment offer."
  },
  "timestamp": "2026-10-12T12:00:00Z"
}
```

### 6.2 Get Recommended Job Matches for Candidate
- **Endpoint:** `GET /api/v1/matching/{candidate_id}/recommendations`
- **Auth:** Bearer Token (Owner of `candidate_id`)
- **Query Parameters:** `min_score` (float, default 60.0), `limit` (int, default 10)
- **Response (200 OK):** Array of `MatchResult` objects ranked descending by `overall_score`.

---

## 7. Assessment Execution Contract (Developer A Sandbox Interface)

### 7.1 Submit Assessment Code
- **Endpoint:** `POST /api/v1/assessments/submit`
- **Auth:** Bearer Token (Candidate)
- **Request Body:**
```json
{
  "assessment_id": "asm_10b29c34",
  "candidate_id": "prof_32c45e67",
  "language": "python",
  "code": "def solve(x):\n    return x * 2\n"
}
```
- **Validation Rules:**
  - `language`: Enum `["python", "javascript"]`.
  - `code`: Max length 65,536 bytes (64 KB).
- **Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "submission_id": "sub_441e89b2",
    "status": "passed",
    "passed_tests": 3,
    "total_tests": 3,
    "execution_time_ms": 12.4,
    "memory_kb": 2048,
    "stdout_excerpt": "All 3 test cases passed.",
    "stderr_excerpt": null,
    "error_message": null,
    "is_mock": true
  },
  "timestamp": "2026-10-12T12:00:00Z"
}
```

---

## 8. Integration Dependency & Ownership Matrix

| Service / Interface | Owner | Primary Consumers | Testing Strategy |
| :--- | :--- | :--- | :--- |
| `AuthenticationService` | Developer B | `api/v1/auth`, `authorization/policies` | Unit tests with test JWT secrets; bcrypt rounds tuned for fast unit testing. |
| `ResumeParser` | Developer B | `pipelines/resume_pipeline` | Unit tests with test PDF/DOCX fixture bytes; assert text sanitization. |
| `LLMProviderInterface` | Developer B | `nlp`, `matching`, `agents` | Mock provider fixture returning deterministic Pydantic schemas; zero real API keys required. |
| `FirecrawlAdapter` | Developer B | `pipelines/job_ingestion_pipeline` | Mock HTTP client returning recorded fixture HTML/Markdown; domain allowlist unit test. |
| `RetrievalService` | Developer B | `matching`, `agents` | In-memory cosine similarity fixture with synthetic embeddings; assert deterministic ranking. |
| `MatchingScorer` | Developer B | `api/v1/matching`, `pipelines/matching_pipeline` | Unit tests for deterministic edge cases (100% match, 0% match, partial overlap). |
| `AssessmentExecutorInterface`| Developer A | `api/v1/assessment`, `services/assessment_service` | `MockAssessmentExecutor` unit test validating isolation and resource capping schemas. |

---

## 9. Contract Change Management
Any modification to this document requires:
1. Written description of schema changes and backward-compatibility impact.
2. Sign-off by both Developer A and Developer B.
3. Updated unit and integration tests verifying new contract boundaries.

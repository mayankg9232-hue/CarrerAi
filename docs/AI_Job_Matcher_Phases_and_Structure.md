# AI Job Matcher — Updated Master Plan, Ownership, Phases, and File Structure

## 1. How Antigravity must work

Act as the technical lead, but explain choices in beginner-friendly language. Inspect the existing repository before editing it. Use project/conversation context you can actually access; state clearly when prior context is unavailable. Do not invent prior decisions.

Build in small phases. **Do not start the next phase until the current phase has at least five distinct test cases, the relevant tests have been run, failures have been fixed or explicitly documented, and the phase report is shown to both developers.** Do not claim tests passed unless they ran. Re-run relevant earlier tests after changes.

Git is for source-control and collaboration from the beginning. **Public hosting/deployment must wait until the final phase.** Do not publish a live app, public API, assessment runner, or public demo early.

## 2. Product goal

AI Job Matcher helps a candidate submit a CV, extracts structured profile information, ingests suitable job listings from permitted sources, matches candidates to roles, explains the match, identifies missing/uncertain skills, and suggests truthful improvements. A future assessment capability may run candidate-submitted code to help verify selected skills. Do not let future ideas bloat the initial implementation, but design interfaces so they can be added cleanly.

Never fabricate jobs, candidate skills, qualifications, or achievements. Matching scores must be explainable and clearly described as estimates, not objective guarantees. Treat CVs, web pages, job descriptions, LLM outputs, and candidate code as untrusted input.

## 3. Developer ownership

### Developer A — project owner

- FastAPI application and backend structure
- HTTP and REST API design
- API contracts and integration coordination
- Pipeline orchestration
- Agentic AI architecture and orchestrator
- Agent tool permissions, policy enforcement, limits, audit events, and agent rulebook
- Authorization and resource ownership checks
- Security engineering and hardening
- Code-execution sandbox architecture and assessment-runner integration
- Docker and reproducible local execution
- Deployment, release checks, and rollback plan

### Developer B — AI/data features

- Authentication and identity verification
- NLP and résumé parsing
- LLM provider adapters and structured model output
- Embeddings and retrieval
- RAG
- Matching engine and match explanations/gap analysis
- Firecrawl integration and permitted job-source adapters

### Shared responsibilities

Both developers agree on API contracts and schemas before dependent code is integrated. Both review integrations, write/maintain tests, and document changes. A owns agent orchestration; B owns the capabilities/tools agents are allowed to call. A owns authorization policy; B builds authentication behind an agreed interface.

**Authentication is who the user is. Authorization is what that user is allowed to do.** Keep them separate.

## 4. Architecture principles

Start as a modular monolith, not microservices. Prefer clear interfaces over hidden coupling.

- FastAPI: HTTP/API layer.
- Pydantic: validate request/response and internal structured data.
- Services/pipelines: deterministic business steps.
- AI components: NLP, LLM integration, retrieval, matching.
- Agents: bounded orchestration only where decisions/tool selection add value.
- Source adapters: isolate Firecrawl and other providers.
- Sandbox: separate service/interface for untrusted candidate code; never run candidate code inside the FastAPI process.
- Repository/storage interfaces: isolate persistence choices. Decide and document the MVP storage choice before relying on it; do not silently add databases or vector databases.

Do not use LangChain/LangGraph just for the label. Select them only if they make the workflow easier to control, test, and maintain. External AI or scraping integrations must be replaceable and mocked in ordinary automated tests. Check current provider plans, free-tier limits, terms, and rate limits before live use.

## 5. Recommended initial file structure

Adapt this to existing code. Do not delete working files or create empty files with no immediate purpose. Implement a file when its phase begins.

```text
ai-job-matcher/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── logging.py
│   │   ├── exceptions.py
│   │   └── security.py
│   ├── api/
│   │   └── v1/
│   │       ├── router.py
│   │       └── endpoints/
│   │           ├── health.py
│   │           ├── auth.py
│   │           ├── resumes.py
│   │           ├── jobs.py
│   │           ├── matching.py
│   │           └── assessments.py
│   ├── schemas/
│   │   ├── auth.py
│   │   ├── resume.py
│   │   ├── job.py
│   │   ├── matching.py
│   │   └── assessment.py
│   ├── authentication/          # Developer B
│   │   ├── service.py
│   │   └── dependencies.py
│   ├── authorization/            # Developer A
│   │   ├── policies.py
│   │   └── dependencies.py
│   ├── services/
│   │   ├── resume_service.py
│   │   ├── job_service.py
│   │   ├── matching_service.py
│   │   └── assessment_service.py
│   ├── pipelines/                # Developer A owns orchestration
│   │   ├── resume_pipeline.py
│   │   ├── job_ingestion_pipeline.py
│   │   └── matching_pipeline.py
│   ├── nlp/                      # Developer B
│   │   ├── resume_parser.py
│   │   ├── skill_extractor.py
│   │   └── normalizer.py
│   ├── integrations/
│   │   ├── llm/                  # Developer B
│   │   │   ├── base.py
│   │   │   ├── primary_provider.py
│   │   │   └── secondary_provider.py
│   │   └── firecrawl/            # Developer B
│   │       ├── client.py
│   │       └── source_adapter.py
│   ├── retrieval/                # Developer B
│   │   ├── embeddings.py
│   │   ├── retriever.py
│   │   └── rag_service.py
│   ├── matching/                 # Developer B
│   │   ├── scorer.py
│   │   ├── explain.py
│   │   └── gap_analysis.py
│   ├── agents/                   # Developer A owns controls/orchestration
│   │   ├── orchestrator.py
│   │   ├── state.py
│   │   ├── tool_registry.py
│   │   ├── policy_gate.py
│   │   ├── budgets.py
│   │   ├── guardrails.py
│   │   ├── audit.py
│   │   └── rulebook.md
│   ├── sandbox/                  # Developer A; separate from AI agent tools
│   │   ├── interface.py
│   │   ├── models.py
│   │   ├── service.py
│   │   ├── limits.py
│   │   └── providers/
│   │       └── docker_executor.py
│   └── repositories/
│       └── interfaces.py
├── sandbox-images/               # only add images we actually support
│   ├── python/
│   │   └── Dockerfile
│   └── README.md
├── dev_ui/                       # rough, local-only test UI
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── frontend/                     # final frontend, after backend acceptance
│   └── README.md
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   ├── security/
│   ├── agents/
│   ├── sandbox/
│   └── fixtures/
├── scripts/
├── docs/
│   ├── architecture.md
│   ├── api-contracts.md
│   ├── phase-plan.md
│   ├── testing-strategy.md
│   ├── collaboration.md
│   └── AI_AGENT_RULEBOOK.md
├── .env.example
├── .gitignore
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── Dockerfile
└── docker-compose.yml
```

The sandbox image shown is only an example for a supported language. Do not add language images until they can be built, pinned, tested, and safely constrained. Never expose a Docker socket or run assessment containers in privileged mode.

## 6. Phase plan and test gates

### Phase 0 — Audit and contracts
**Lead:** Antigravity coordinates both developers. **No broad code changes. No deployment.**

Inspect the current repository, environment instructions, dependencies, working functionality, tests, and existing folder structure. Map existing code to the structure above. Identify blockers and recommend the MVP persistence approach. Draft API/data contracts and agree ownership. Add the rulebook to `docs/AI_AGENT_RULEBOOK.md` and identify which controls must be enforced in code.

**Five different audit checks:** (1) repository/tree and entry point identified; (2) dependency/environment setup inspected; (3) existing tests and test command identified; (4) secrets and ignored files checked without printing secrets; (5) module ownership/API-contract gaps documented. If code already exists, run appropriate baseline checks and report actual outcomes.

**Gate:** show the audit and plan to both developers before Phase 1.

### Phase 1 — FastAPI, HTTP/REST, basic backend, and rough test UI
**Lead:** A.

Establish application startup, health endpoint, API version prefix, consistent response/error conventions, Pydantic validation, configuration loading, logging, and a basic local `dev_ui/`. This UI is for testing only. Do not build the final frontend now.

**At least five distinct tests:** health success; valid request/response schema; invalid body rejected; unknown route handled; unexpected/invalid input handled without crashing the app. Add more if the feature scope requires them.

### Phase 2 — Authentication, authorization, API access control
**Authentication lead:** B. **Authorization/security lead:** A.

Agree the identity schema and interface first. Protect routes, enforce roles/resource ownership, keep secrets out of Git, validate inputs, and return safe errors. No placeholder identity mechanism may be described as production-ready.

**At least five distinct tests:** valid authentication; missing/invalid credentials; insufficient role; cross-user resource access denied; malformed or oversized input rejected safely.

### Phase 3 — Job and résumé pipelines/source adapters
**Pipeline orchestration lead:** A. **Firecrawl/NLP lead:** B.

Agree normalized `Job` and `CandidateProfile` schemas. Add supported Firecrawl collection through an adapter, respectful rate limits, timeouts, provenance, collected-at timestamps, normalization, deduplication, and stale/expired listing handling. Parse supported CV files safely and return structured, validated fields. Use mocked source responses in normal tests; do not consume free-tier credits on every test.

**At least five distinct tests:** duplicate job normalization; malformed listing; source timeout/rate limit; unreadable CV; CV missing optional fields. Add tests for invalid files and stale listings as relevant.

### Phase 4 — LLM providers and structured output
**Lead:** B. **Integration contract:** shared.

Add provider adapters behind one interface. The planned two-model pattern is a primary model for a defined task and a secondary model for a limited review/fallback task—not two models called automatically on every request. Validate output, bound retries/timeouts, record provider/model metadata without logging secrets or sensitive CV contents, and mock models in tests.

**At least five distinct tests:** valid structured output; malformed model output; timeout; absent credentials; secondary model unavailable/fallback path.

### Phase 5 — Matching, embeddings, RAG, gap analysis
**Lead:** B. **API and pipeline integration:** A.

Produce explainable candidate-job recommendations, relevant overlaps, gaps, uncertainties, and improvement suggestions. Define scoring factors and caveats. Use retrieval/embeddings only where they improve relevance; use grounded source content and do not let retrieved instructions override system policy.

**At least five distinct tests:** strong match; weak match; partial skill overlap; missing information; semantically similar but substantively wrong job. Verify explanations agree with the actual score inputs.

### Phase 6 — Agentic AI, tool permissions, rulebook, and bounded workflows
**Lead:** A. **Tools/capabilities:** B.

This is a dedicated milestone, not a small add-on. Create an orchestrator with explicit states, an allowlisted tool registry, a policy gate, step/time/token/tool budgets, bounded retries, structured tool arguments/results, audit events, stop conditions, and a final response validator. Agents must call B's approved interfaces, not access provider credentials or data stores directly. Read and implement `docs/AI_AGENT_RULEBOOK.md` before enabling tool execution.

Use deterministic pipelines for reliable ordered work; only use an agent for decisions requiring flexible planning or tool selection. Never allow arbitrary shell commands, unrestricted network access, self-modification of policies, or uncontrolled agent-to-agent loops.

**At least five distinct tests:** normal tool workflow; unauthorized tool call blocked; malformed tool arguments rejected; step/cost/time budget stops workflow; prompt injection inside CV/job text ignored as untrusted data. Also test provider failure and fabricated/unsupported claims if applicable.

### Phase 7 — Assessment sandbox design and controlled interface
**Lead:** A.

Create the assessment request/result schemas, language allowlist, time/memory/output limits, executor interface, isolated-workspace lifecycle, and a mock executor for local development. **Do not execute submitted code inside FastAPI, the host Python process, or a general-purpose shell.** Until the hardened executor exists and is reviewed, the assessment feature must remain disabled or use only the mock executor.

**At least five distinct tests:** supported language accepted; unsupported language rejected; invalid/missing submission rejected; resource limit validated; timeout/runner failure returns a safe result. Add tests ensuring candidate code is never passed to host `exec`/`eval` or host shell execution.

### Phase 8 — Docker and isolated code execution
**Lead:** A.

Implement reproducible Docker-based local execution only after the sandbox interface and constraints are reviewed. The executor must use an unprivileged, short-lived container with no network, strict CPU/memory/PID/time/output/file limits, a restricted writable temp directory, no secrets mounted, no host filesystem access, no Docker socket, and automatic cleanup. Pin base images and document supported language/runtime versions. Do not claim Docker alone guarantees complete isolation. Before public use, obtain an appropriate security review; high-risk public code execution may require stronger isolation such as microVMs or a specialist execution service.

**At least five distinct tests:** benign code returns expected output; infinite loop times out; excessive output is capped; memory/CPU overuse is stopped; attempted network/filesystem/credential access is blocked. Also test that the container is cleaned up after both success and failure.

### Phase 9 — Full integration and security acceptance
**Lead:** shared; A coordinates.

Integrate the core candidate journey and all agreed interfaces. Check authorization at every protected route, prompt-injection handling, upload limits, third-party failures, data privacy, rate limits, logging redaction, and the sandbox boundary. Document incomplete future features.

**At least five distinct end-to-end/security tests:** authorized CV-to-match journey; unauthorized/cross-user request denied; scraper/model outage; malicious or malformed CV/job content; assessment isolation/limit check. Run the complete regression suite.

### Phase 10 — Backend acceptance and final frontend
**Frontend starts only after backend acceptance.**

Confirm API contracts are stable and the MVP works locally on both laptops. Then use Figma for design planning and implement the final frontend against real APIs, including loading, empty, error, and success states. Keep the rough developer UI if useful for debugging.

**At least five distinct tests:** initial page state; CV submission; match result and explanation display; API failure; no-matches/empty state. Add role/authentication and assessment UI tests if those features are included.

### Phase 11 — Final production-readiness and deployment
**Lead:** A; shared review. **Only phase where live public deployment is permitted.**

Run all test suites, review secrets/configuration, dependency vulnerabilities, CORS, authentication/authorization, uploads, rate limits, logging, data retention, external-service terms/costs, sandbox security, and recovery/rollback. Choose hosting based on current needs and verified pricing. Deploy only after both developers approve the release checklist.

**At least five release checks:** production health; valid core API path; unauthorized access blocked; invalid/missing secret configuration handled safely; critical end-to-end user journey works on the deployed version. Add assessment-isolation verification before enabling public assessments. Do not claim success without observing the result.

## 7. Per-phase report template

After each phase, report:

1. What changed and why.
2. Files added/modified.
3. How Developer A's and B's modules connect.
4. Test name, scenario, expected result, actual result, pass/fail.
5. Commands used to run tests.
6. Failures fixed, skipped tests, and remaining limitations.
7. Manual verification steps.
8. Whether the phase gate passed.

Do not automatically proceed to the next phase. Wait for both developers to review and authorize continuation.

## 8. Two-laptop collaboration

Use a shared Git remote as the source of truth. Use separate local virtual environments; commit dependency files, never virtual-environment folders. Use clear feature branches or explicitly assigned files; avoid simultaneous edits to shared contracts/orchestrators. Agree on input/output schemas before parallel implementation. Make small commits, review before merge, and never overwrite unreviewed work or force-push shared branches. Do not confuse using GitHub for source control with deploying a public application.

## First instruction to Antigravity

**Perform Phase 0 only.** Inspect the repository and the context available to you, then produce the audit, ownership map, structure mapping, API contracts, sandbox boundary plan, agent rulebook plan, storage recommendation, dependencies between phases, and five baseline/audit checks. Do not make broad code changes, do not implement all phases, and do not deploy. Wait for review before Phase 1.

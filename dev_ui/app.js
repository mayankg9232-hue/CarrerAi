document.addEventListener("DOMContentLoaded", () => {
  const statusPill = document.getElementById("statusPill");
  const latencyPill = document.getElementById("latencyPill");
  const requestIdSpan = document.getElementById("headerRequestId");
  const responseJson = document.getElementById("responseJson");

  async function executeRequest(url, options = {}) {
    statusPill.textContent = "Status: Sending...";
    statusPill.style.color = "#f59e0b";
    responseJson.textContent = "// Request in progress...";

    const startTime = performance.now();
    try {
      const response = await fetch(url, options);
      const elapsed = Math.round(performance.now() - startTime);
      const reqId = response.headers.get("X-Request-ID") || "—";
      requestIdSpan.textContent = reqId;
      latencyPill.textContent = `Time: ${elapsed}ms`;

      statusPill.textContent = `Status: ${response.status} ${response.statusText}`;
      if (response.ok) {
        statusPill.style.color = "#10b981";
      } else if (response.status < 500) {
        statusPill.style.color = "#f59e0b";
      } else {
        statusPill.style.color = "#ef4444";
      }

      const data = await response.json();
      responseJson.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      const elapsed = Math.round(performance.now() - startTime);
      latencyPill.textContent = `Time: ${elapsed}ms`;
      statusPill.textContent = "Status: Network Error";
      statusPill.style.color = "#ef4444";
      responseJson.textContent = JSON.stringify({ error: err.message }, null, 2);
    }
  }

  // 1. Health Check
  document.getElementById("btnHealthCheck").addEventListener("click", () => {
    executeRequest("/api/v1/health");
  });

  // 2. Valid Echo
  document.getElementById("btnSendValidEcho").addEventListener("click", () => {
    const msg = document.getElementById("echoMessage").value || "Test valid";
    const tag = document.getElementById("echoTag").value || "sample_tag";
    executeRequest("/api/v1/test/echo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg, tag: tag }),
    });
  });

  // 3. Invalid Echo (Triggers 422)
  document.getElementById("btnSendInvalidEcho").addEventListener("click", () => {
    executeRequest("/api/v1/test/echo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "x", tag: "invalid tag with spaces" }),
    });
  });

  // 4. Trigger 404
  document.getElementById("btnTriggerNotFound").addEventListener("click", () => {
    executeRequest("/api/v1/non-existent-route");
  });

  // 5. Trigger 500
  document.getElementById("btnTrigger500").addEventListener("click", () => {
    executeRequest("/api/v1/test/unhandled-error");
  });

  // --- Phase 2 Authentication (Developer B) ---
  let storedBearerToken = null;

  // Register
  document.getElementById("btnRegister").addEventListener("click", () => {
    const email = document.getElementById("regEmail").value;
    const password = document.getElementById("regPassword").value;
    const fullName = document.getElementById("regName").value;
    const role = document.getElementById("regRole").value;

    executeRequest("/api/v1/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, full_name: fullName, role }),
    });
  });

  // Login
  document.getElementById("btnLogin").addEventListener("click", async () => {
    const email = document.getElementById("loginEmail").value;
    const password = document.getElementById("loginPassword").value;

    statusPill.textContent = "Status: Authenticating...";
    statusPill.style.color = "#f59e0b";
    responseJson.textContent = "// Logging in...";

    const startTime = performance.now();
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const elapsed = Math.round(performance.now() - startTime);
      latencyPill.textContent = `Time: ${elapsed}ms`;
      statusPill.textContent = `Status: ${response.status} ${response.statusText}`;
      const data = await response.json();
      responseJson.textContent = JSON.stringify(data, null, 2);

      if (response.ok && data.data && data.data.access_token) {
        storedBearerToken = data.data.access_token;
        statusPill.style.color = "#10b981";
        statusPill.textContent += " (Token Saved)";
      } else {
        statusPill.style.color = "#ef4444";
      }
    } catch (err) {
      statusPill.textContent = "Status: Error";
      statusPill.style.color = "#ef4444";
      responseJson.textContent = JSON.stringify({ error: err.message }, null, 2);
    }
  });

  // GET /auth/me (Protected)
  document.getElementById("btnGetMe").addEventListener("click", () => {
    const headers = {};
    if (storedBearerToken) {
      headers["Authorization"] = `Bearer ${storedBearerToken}`;
    }
    executeRequest("/api/v1/auth/me", { headers });
  });

  // GET /auth/recruiter-only (Role Protected)
  document.getElementById("btnRecruiterCheck").addEventListener("click", () => {
    const headers = {};
    if (storedBearerToken) {
      headers["Authorization"] = `Bearer ${storedBearerToken}`;
    }
    executeRequest("/api/v1/auth/recruiter-only", { headers });
  });

  // --- Firebase Web SDK Client Handlers ---
  let firebaseAuthInstance = null;

  async function getOrInitFirebaseAuth() {
    if (firebaseAuthInstance) return firebaseAuthInstance;
    const resp = await fetch("/api/v1/auth/firebase-config");
    const json = await resp.json();
    if (!json.success || !json.data.apiKey) {
      throw new Error("Firebase API key not configured in .env on server");
    }
    const app = window.FirebaseSDK.initializeApp(json.data);
    firebaseAuthInstance = window.FirebaseSDK.getAuth(app);
    return firebaseAuthInstance;
  }

  // Fetch Firebase Config
  document.getElementById("btnFirebaseConfig").addEventListener("click", () => {
    executeRequest("/api/v1/auth/firebase-config");
  });

  // Firebase Create User
  document.getElementById("btnFirebaseRegister").addEventListener("click", async () => {
    const email = document.getElementById("fbEmail").value;
    const password = document.getElementById("fbPassword").value;
    statusPill.textContent = "Status: Firebase registering...";
    statusPill.style.color = "#f59e0b";

    try {
      const auth = await getOrInitFirebaseAuth();
      const userCred = await window.FirebaseSDK.createUserWithEmailAndPassword(auth, email, password);
      const idToken = await userCred.user.getIdToken();

      // Exchange with backend
      await executeRequest("/api/v1/auth/firebase-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken, role: "candidate" }),
      });
    } catch (err) {
      statusPill.textContent = "Status: Firebase Error";
      statusPill.style.color = "#ef4444";
      responseJson.textContent = JSON.stringify({ error: err.message, code: err.code }, null, 2);
    }
  });

  // Firebase Sign In
  document.getElementById("btnFirebaseLogin").addEventListener("click", async () => {
    const email = document.getElementById("fbEmail").value;
    const password = document.getElementById("fbPassword").value;
    statusPill.textContent = "Status: Firebase authenticating...";
    statusPill.style.color = "#f59e0b";

    try {
      const auth = await getOrInitFirebaseAuth();
      const userCred = await window.FirebaseSDK.signInWithEmailAndPassword(auth, email, password);
      const idToken = await userCred.user.getIdToken();

      // Exchange with backend
      const startTime = performance.now();
      const response = await fetch("/api/v1/auth/firebase-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken, role: "candidate" }),
      });
      const elapsed = Math.round(performance.now() - startTime);
      latencyPill.textContent = `Time: ${elapsed}ms`;
      statusPill.textContent = `Status: ${response.status} ${response.statusText}`;

      const data = await response.json();
      responseJson.textContent = JSON.stringify(data, null, 2);

      if (response.ok && data.data && data.data.access_token) {
        storedBearerToken = data.data.access_token;
        statusPill.style.color = "#10b981";
        statusPill.textContent += " (Token Saved)";
      }
    } catch (err) {
      statusPill.textContent = "Status: Firebase Error";
      statusPill.style.color = "#ef4444";
      responseJson.textContent = JSON.stringify({ error: err.message, code: err.code }, null, 2);
    }
  });

  // Phase 3 Resume Upload
  const btnUploadResume = document.getElementById("btnUploadResume");
  if (btnUploadResume) {
    btnUploadResume.addEventListener("click", async () => {
      const fileInput = document.getElementById("cvFileInput");
      if (!fileInput.files || fileInput.files.length === 0) {
        alert("Please select a PDF, DOCX, or TXT file to upload.");
        return;
      }
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);

      const headers = {};
      if (storedBearerToken) {
        headers["Authorization"] = `Bearer ${storedBearerToken}`;
      }

      await executeRequest("/api/v1/resumes/upload", {
        method: "POST",
        headers,
        body: formData,
      });
    });
  }

  // Phase 3 List Resumes
  const btnListResumes = document.getElementById("btnListResumes");
  if (btnListResumes) {
    btnListResumes.addEventListener("click", async () => {
      const headers = {};
      if (storedBearerToken) {
        headers["Authorization"] = `Bearer ${storedBearerToken}`;
      }
      await executeRequest("/api/v1/resumes", { method: "GET", headers });
    });
  }

  // Phase 3 Ingest Job
  const btnIngestJob = document.getElementById("btnIngestJob");
  if (btnIngestJob) {
    btnIngestJob.addEventListener("click", async () => {
      const url = document.getElementById("jobSourceUrl").value.trim();
      const headers = { "Content-Type": "application/json" };
      if (storedBearerToken) {
        headers["Authorization"] = `Bearer ${storedBearerToken}`;
      }
      await executeRequest("/api/v1/jobs/ingest", {
        method: "POST",
        headers,
        body: JSON.stringify({ source_urls: [url], max_pages: 1 }),
      });
    });
  }

  // Phase 3 Search Jobs
  const btnSearchJobs = document.getElementById("btnSearchJobs");
  if (btnSearchJobs) {
    btnSearchJobs.addEventListener("click", async () => {
      const q = document.getElementById("jobSearchQuery").value.trim();
      const skills = document.getElementById("jobSearchSkills").value.trim();
      const params = new URLSearchParams();
      if (q) params.append("q", q);
      if (skills) params.append("skills", skills);

      const qs = params.toString() ? `?${params.toString()}` : "";
      const headers = {};
      if (storedBearerToken) {
        headers["Authorization"] = `Bearer ${storedBearerToken}`;
      }
      await executeRequest(`/api/v1/jobs${qs}`, { method: "GET", headers });
    });
  }

  // --- Phase 4 LLM Actions (Developer A) ---
  const btnLlmExtractResume = document.getElementById("btnLlmExtractResume");
  if (btnLlmExtractResume) {
    btnLlmExtractResume.addEventListener("click", async () => {
      const text = document.getElementById("llmResumeTextInput").value.trim();
      const headers = { "Content-Type": "application/json" };
      if (storedBearerToken) headers["Authorization"] = `Bearer ${storedBearerToken}`;
      await executeRequest("/api/v1/llm/extract-resume", {
        method: "POST",
        headers,
        body: JSON.stringify({ resume_text: text }),
      });
    });
  }

  const btnLlmExplainMatch = document.getElementById("btnLlmExplainMatch");
  if (btnLlmExplainMatch) {
    btnLlmExplainMatch.addEventListener("click", async () => {
      const text = document.getElementById("llmResumeTextInput").value.trim();
      const headers = { "Content-Type": "application/json" };
      if (storedBearerToken) headers["Authorization"] = `Bearer ${storedBearerToken}`;
      await executeRequest("/api/v1/llm/explain-match", {
        method: "POST",
        headers,
        body: JSON.stringify({
          resume_text: text,
          job_description: "Senior Python Developer with FastAPI and Docker experience.",
        }),
      });
    });
  }

  const btnLlmReview = document.getElementById("btnLlmReview");
  if (btnLlmReview) {
    btnLlmReview.addEventListener("click", async () => {
      const headers = { "Content-Type": "application/json" };
      if (storedBearerToken) headers["Authorization"] = `Bearer ${storedBearerToken}`;
      await executeRequest("/api/v1/llm/review", {
        method: "POST",
        headers,
        body: JSON.stringify({
          task_context: "Audit extracted skills for veracity against source CV",
          output_to_review: {
            extracted_skills: ["Python", "FastAPI", "Docker"],
            candidate_claimed_level: "Senior",
          },
        }),
      });
    });
  }

  const btnLlmStatus = document.getElementById("btnLlmStatus");
  if (btnLlmStatus) {
    btnLlmStatus.addEventListener("click", async () => {
      const headers = {};
      if (storedBearerToken) headers["Authorization"] = `Bearer ${storedBearerToken}`;
      await executeRequest("/api/v1/llm/status", { method: "GET", headers });
    });
  }

  // --- Phase 5 Matching Actions (Developer A) ---
  const btnMatchCandidateJob = document.getElementById("btnMatchCandidateJob");
  if (btnMatchCandidateJob) {
    btnMatchCandidateJob.addEventListener("click", async () => {
      const candidateId = document.getElementById("matchCandidateId").value.trim();
      const jobId = document.getElementById("matchJobId").value.trim();
      if (!candidateId || !jobId) {
        alert("Please enter both Candidate Profile ID and Job ID");
        return;
      }
      const headers = {};
      if (storedBearerToken) headers["Authorization"] = `Bearer ${storedBearerToken}`;
      await executeRequest(`/api/v1/matching/${encodeURIComponent(candidateId)}/jobs/${encodeURIComponent(jobId)}`, {
        method: "POST",
        headers,
      });
    });
  }

  const btnGetRecommendations = document.getElementById("btnGetRecommendations");
  if (btnGetRecommendations) {
    btnGetRecommendations.addEventListener("click", async () => {
      const candidateId = document.getElementById("matchCandidateId").value.trim();
      if (!candidateId) {
        alert("Please enter Candidate Profile ID");
        return;
      }
      const minScore = document.getElementById("matchMinScore").value.trim() || "60";
      const limit = document.getElementById("matchLimit").value.trim() || "10";
      const headers = {};
      if (storedBearerToken) headers["Authorization"] = `Bearer ${storedBearerToken}`;
      await executeRequest(`/api/v1/matching/${encodeURIComponent(candidateId)}/recommendations?min_score=${encodeURIComponent(minScore)}&limit=${encodeURIComponent(limit)}`, {
        method: "GET",
        headers,
      });
    });
  }

  const btnGetRagContext = document.getElementById("btnGetRagContext");
  if (btnGetRagContext) {
    btnGetRagContext.addEventListener("click", async () => {
      const candidateId = document.getElementById("matchCandidateId").value.trim();
      const jobId = document.getElementById("matchJobId").value.trim();
      if (!candidateId || !jobId) {
        alert("Please enter both Candidate Profile ID and Job ID");
        return;
      }
      const headers = {};
      if (storedBearerToken) headers["Authorization"] = `Bearer ${storedBearerToken}`;
      await executeRequest(`/api/v1/matching/${encodeURIComponent(candidateId)}/jobs/${encodeURIComponent(jobId)}/rag-context`, {
        method: "GET",
        headers,
      });
    });
  }

  // Auto-ping health on initial load
  executeRequest("/api/v1/health");

});

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

  // Auto-ping health on initial load
  executeRequest("/api/v1/health");
});

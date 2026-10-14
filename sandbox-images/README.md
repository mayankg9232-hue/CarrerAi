# Sandbox Images — Candidate Code Assessment Isolation (Phase 8)

This directory defines pinned, minimal, hardened container images used by Developer A's `DockerSandboxExecutor` to safely execute candidate assessment code submissions.

---

## 1. Supported Images and Runtimes

| Language | Image Tag | Base Image | Non-Root User | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Python** | `ai-job-matcher-sandbox-python:3.11` | `python:3.11.14-alpine3.20` | `10001:10001` (`sandboxuser`) | Active |
| **JavaScript** | *Planned (Phase 8+)* | `node:20-alpine` | `10001:10001` | In Development |
| **TypeScript** | *Planned (Phase 8+)* | `node:20-alpine` | `10001:10001` | In Development |

---

## 2. Hardening Constraints & Isolation Flags

When launching execution containers, `DockerSandboxExecutor` mandates the following non-negotiable security flags:

1. **Network Lockdown (`--network none`)**:
   - Containers have no access to the external internet, local network, host loopback, or metadata services.
2. **Capability Dropping (`--cap-drop ALL`)**:
   - Drops all Linux root capabilities (`CAP_SYS_ADMIN`, `CAP_NET_RAW`, etc.).
3. **Privilege Escalation Prevention (`--security-opt no-new-privileges:true`)**:
   - Prevents setuid binaries or sub-processes from acquiring additional privileges.
4. **Read-Only Root Filesystem (`--read-only`)**:
   - The container's root filesystem cannot be modified.
5. **No Host Filesystem Access / Zero Secret Mounting**:
   - Neither the host root filesystem, Docker socket (`/var/run/docker.sock`), application secrets, nor `.env` are ever mounted into the container.
   - Only the ephemeral, candidate-specific temporary workspace directory is mounted (`-v {workspace}:/workspace:rw`).
6. **Strict Resource Bounds**:
   - Memory ceiling: `--memory={limit}m --memory-swap={limit}m` (kills process on OOM).
   - CPU throttling: `--cpus=1.0`.
   - Process limit: `--pids-limit=32` (defends against fork bombs).
   - Wall-clock timeout: enforced via Python `asyncio.wait_for(...)` with automatic `docker rm -f` cleanup.
7. **Ephemeral Lifecycle**:
   - Containers are launched with `--rm` and explicitly removed in a `finally:` block upon completion, timeout, or cancellation.

---

## 3. How to Build the Sandbox Images Locally

```bash
# Build the Python 3.11 hardened sandbox image
docker build -t ai-job-matcher-sandbox-python:3.11 ./sandbox-images/python
```

---

## 4. Production Security Disclaimer

> [!WARNING]
> While Docker containers with dropped capabilities, read-only root filesystems, and disabled networking provide effective isolation for local testing and controlled benchmarks, containers share the host Linux kernel.
> 
> For **untrusted, adversarial public multi-tenant execution** in production (Phase 11), a stronger virtualization boundary such as **gVisor (runsc)**, **Firecracker microVMs**, or a managed execution service (e.g. AWS Lambda / Fly Machines) must be used.

# Hardened, unprivileged sandbox execution container for Python assessments (Phase 8)
# Pinned official minimal Alpine base image
FROM python:3.11.14-alpine3.20

# 1. Remove dangerous or unnecessary networking utilities if present
RUN rm -f /usr/bin/wget || true

# 2. Create unprivileged, non-login user and group (UID/GID 10001)
RUN addgroup -g 10001 -S sandboxgroup && \
    adduser -u 10001 -S -G sandboxgroup -s /sbin/nologin -h /workspace sandboxuser

# 3. Create isolated workspace directory
WORKDIR /workspace
RUN chown -R sandboxuser:sandboxgroup /workspace && \
    chmod 755 /workspace

# 4. Switch permanently to unprivileged user
USER 10001:10001

# 5. Disable Python byte-code writing and buffering for strict deterministic execution
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random

# 6. Default command to execute candidate script
CMD ["python", "solution.py"]

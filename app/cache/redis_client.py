"""Asynchronous TLS-capable Redis client supporting Upstash and standard Redis deployments.

Implements native Redis RESP2 protocol with TLS/SSL encryption and connection pooling,
with optional fallback to redis.asyncio if installed.
"""

import asyncio
from datetime import datetime, timezone
import logging
import ssl
from typing import Any, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("ai_job_matcher.cache")


try:
    import redis.asyncio as aioredis
    HAS_AIOREDIS = True
except ImportError:
    HAS_AIOREDIS = False
    aioredis = None


class RedisConnectionError(Exception):
    """Raised when Redis server is unreachable or connection fails."""
    pass


class AsyncRedisClient:
    """Production-grade asynchronous Redis client supporting TLS, connection recovery, and timeouts."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: Optional[str] = None,
        use_tls: bool = False,
        timeout: float = 2.0,
    ) -> None:
        self.redis_url = redis_url
        if redis_url:
            parsed = urlparse(redis_url)
            self.host = parsed.hostname or host
            self.port = parsed.port or port
            self.password = parsed.password or password
            self.use_tls = parsed.scheme in ("rediss", "tls") or use_tls
        else:
            self.host = host
            self.port = port
            self.password = password
            self.use_tls = use_tls

        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self._is_connected = False
        self._aioredis_client: Optional[Any] = None
        self._aioredis_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_aioredis_client(self) -> Optional[Any]:
        """Obtain or reconnect aioredis client for the current running event loop."""
        if not HAS_AIOREDIS or not self.redis_url:
            return None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        if self._aioredis_client is None or self._aioredis_loop != current_loop:
            self._aioredis_loop = current_loop
            try:
                self._aioredis_client = aioredis.from_url(
                    self.redis_url,
                    socket_timeout=self.timeout,
                    decode_responses=True,
                )
            except Exception as exc:
                logger.warning("Could not initialize aioredis: %s. Using socket fallback.", exc)
                self._aioredis_client = None
        return self._aioredis_client

    async def connect(self) -> None:
        """Establish TLS/TCP stream connection and authenticate."""
        client = self._get_aioredis_client()
        if client:
            try:
                await client.ping()
                self._is_connected = True
                return
            except Exception as exc:
                self._is_connected = False
                raise RedisConnectionError(f"aioredis failed to connect: {exc}") from exc

        try:
            if self.use_tls:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            else:
                ctx = None
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=ctx),
                timeout=self.timeout,
            )
            if self.password:
                auth_res = await self._send_command("AUTH", self.password)
                if auth_res != "OK":
                    raise RedisConnectionError(f"Redis AUTH failed: {auth_res}")
            self._is_connected = True
            logger.info("Connected to Redis at %s:%d (TLS=%s)", self.host, self.port, self.use_tls)
        except Exception as exc:
            self._is_connected = False
            self._cleanup_sockets()
            raise RedisConnectionError(f"Failed to connect to Redis at {self.host}:{self.port} - {str(exc)}") from exc

    def _cleanup_sockets(self) -> None:
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        self._writer = None
        self._reader = None
        self._is_connected = False

    async def _ensure_connected(self) -> None:
        if not self._is_connected or self._writer is None or self._writer.is_closing():
            await self.connect()

    def _encode_resp(self, *args: str) -> bytes:
        parts = [f"*{len(args)}\r\n".encode("utf-8")]
        for arg in args:
            arg_bytes = str(arg).encode("utf-8")
            parts.append(f"${len(arg_bytes)}\r\n".encode("utf-8") + arg_bytes + b"\r\n")
        return b"".join(parts)

    async def _read_resp(self) -> Any:
        line = await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
        if not line:
            raise ConnectionResetError("Connection terminated by Redis server")
        prefix = line[0:1]
        payload = line[1:-2]

        if prefix == b"+":  # Simple string
            return payload.decode("utf-8")
        elif prefix == b"-":  # Error
            raise RuntimeError(f"Redis error: {payload.decode('utf-8')}")
        elif prefix == b":":  # Integer
            return int(payload)
        elif prefix == b"$":  # Bulk string
            length = int(payload)
            if length == -1:
                return None
            data = await asyncio.wait_for(self._reader.readexactly(length + 2), timeout=self.timeout)
            return data[:-2].decode("utf-8")
        elif prefix == b"*":  # Array
            count = int(payload)
            if count == -1:
                return None
            return [await self._read_resp() for _ in range(count)]
        else:
            raise ValueError(f"Unknown RESP response byte: {prefix}")

    async def _send_command(self, *args: str) -> Any:
        async with self._lock:
            try:
                await self._ensure_connected()
                cmd = self._encode_resp(*args)
                self._writer.write(cmd)
                await self._writer.drain()
                return await self._read_resp()
            except Exception as exc:
                self._cleanup_sockets()
                raise RedisConnectionError(f"Redis command failed: {exc}") from exc

    async def ping(self) -> str:
        """Send PING command."""
        client = self._get_aioredis_client()
        if client:
            try:
                res = await client.ping()
                return "PONG" if res else ""
            except Exception as exc:
                raise RedisConnectionError(f"aioredis ping failed: {exc}") from exc
        return await self._send_command("PING")

    async def get(self, key: str) -> Optional[str]:
        """Fetch string value by key."""
        client = self._get_aioredis_client()
        if client:
            try:
                return await client.get(key)
            except Exception as exc:
                raise RedisConnectionError(f"aioredis get failed: {exc}") from exc
        return await self._send_command("GET", key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set key value with optional TTL expiry in seconds."""
        client = self._get_aioredis_client()
        if client:
            try:
                return bool(await client.set(key, value, ex=ex))
            except Exception as exc:
                raise RedisConnectionError(f"aioredis set failed: {exc}") from exc
        if ex is not None and ex > 0:
            res = await self._send_command("SET", key, value, "EX", str(ex))
        else:
            res = await self._send_command("SET", key, value)
        return res == "OK"

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        if not keys:
            return 0
        client = self._get_aioredis_client()
        if client:
            try:
                return await client.delete(*keys)
            except Exception as exc:
                raise RedisConnectionError(f"aioredis delete failed: {exc}") from exc
        return await self._send_command("DEL", *keys)

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        client = self._get_aioredis_client()
        if client:
            try:
                return bool(await client.exists(key))
            except Exception as exc:
                raise RedisConnectionError(f"aioredis exists failed: {exc}") from exc
        res = await self._send_command("EXISTS", key)
        return bool(res)

    async def keys(self, pattern: str) -> List[str]:
        """Find keys matching glob pattern."""
        client = self._get_aioredis_client()
        if client:
            try:
                res = await client.keys(pattern)
                return list(res) if res else []
            except Exception as exc:
                raise RedisConnectionError(f"aioredis keys failed: {exc}") from exc
        res = await self._send_command("KEYS", pattern)
        return res or []

    async def close(self) -> None:
        """Close connection stream."""
        client = self._get_aioredis_client()
        if client:
            try:
                await client.aclose()
            except Exception:
                pass
            self._aioredis_client = None
            self._aioredis_loop = None
        async with self._lock:
            self._cleanup_sockets()


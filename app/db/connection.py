"""Database connection manager with connection lifecycle, foreign keys, and transaction management.

Supports PostgreSQL (via asyncpg connection pooling) and SQLite (with WAL mode and foreign keys).
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import logging
import os
import sqlite3
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union
import urllib.parse

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False
    asyncpg = None

from app.core.config import Settings, get_settings
from app.db.schema import (
    CREATE_CANDIDATE_PROFILES_TABLE,
    CREATE_JOBS_TABLE,
    CREATE_MATCH_RECORDS_TABLE,
    CREATE_SCRAPING_LOCKS_TABLE,
    CREATE_SCRAPING_RUNS_TABLE,
    CREATE_SCRAPING_SOURCES_TABLE,
    CREATE_USERS_TABLE,
)

logger = logging.getLogger("ai_job_matcher.db")


class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


class UniqueConstraintViolationError(DatabaseError):
    """Raised when a unique constraint is violated (e.g. duplicate email, duplicate job hash)."""
    pass


class DatabaseManager:
    """Thread-safe asynchronous database manager supporting transactions and connection pooling.

    Seamlessly routes queries to PostgreSQL (via asyncpg) or SQLite based on DATABASE_URL.
    """

    def __init__(self, database_url: Optional[str] = None, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.database_url = database_url or self.settings.DATABASE_URL
        self.is_postgres = bool(
            self.database_url
            and (self.database_url.startswith("postgres://") or self.database_url.startswith("postgresql://"))
        )
        self._db_path = self._parse_db_path(self.database_url)
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._pg_pool: Optional[Any] = None

    def _parse_db_path(self, url: str) -> str:
        """Extract filesystem path from SQLite database URL or return memory/path."""
        if "sqlite" in url:
            parts = url.split(":///")
            if len(parts) > 1:
                return parts[1]
            return "app_dev.db"
        return "app_dev.db"

    @staticmethod
    def _to_pg_query(query: str) -> str:
        """Convert SQLite '?' positional placeholders to PostgreSQL '$1, $2, ...' placeholders."""
        parts = query.split("?")
        if len(parts) <= 1:
            return query
        output = []
        for i, part in enumerate(parts[:-1]):
            output.append(part)
            output.append(f"${i + 1}")
        output.append(parts[-1])
        return "".join(output)

    def _get_raw_connection(self) -> sqlite3.Connection:
        """Create a configured SQLite connection with row factories and foreign keys enabled."""
        conn = sqlite3.connect(
            self._db_path,
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    @staticmethod
    def _clean_postgres_url(url: str) -> str:
        """Strip asyncpg-unsupported query parameters (like channel_binding) while preserving sslmode."""
        parsed = urllib.parse.urlsplit(url)
        query_params = urllib.parse.parse_qsl(parsed.query)
        filtered = [(k, v) for k, v in query_params if k.lower() not in ("channel_binding",)]
        clean_query = urllib.parse.urlencode(filtered)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, clean_query, parsed.fragment))

    async def initialize(self) -> None:
        """Execute DDL to create tables and indexes idempotently."""
        async with self._init_lock:
            if self._initialized:
                return

            if self.is_postgres:
                if not HAS_ASYNCPG:
                    raise DatabaseError("asyncpg library is required for PostgreSQL database connections.")

                try:
                    clean_url = self._clean_postgres_url(self.database_url)
                    self._pg_pool = await asyncpg.create_pool(
                        clean_url,
                        min_size=1,
                        max_size=10,
                        timeout=10.0,
                        statement_cache_size=0,
                    )
                    async with self._pg_pool.acquire() as conn:
                        for ddl in [
                            CREATE_USERS_TABLE,
                            CREATE_JOBS_TABLE,
                            CREATE_CANDIDATE_PROFILES_TABLE,
                            CREATE_MATCH_RECORDS_TABLE,
                            CREATE_SCRAPING_RUNS_TABLE,
                            CREATE_SCRAPING_SOURCES_TABLE,
                            CREATE_SCRAPING_LOCKS_TABLE,
                        ]:
                            for stmt in ddl.strip().split(";"):
                                stmt = stmt.strip()
                                if stmt:
                                    await conn.execute(stmt)
                        # PostgreSQL migration: add missing columns to jobs table idempotently
                        for col_stmt in [
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS external_job_id VARCHAR(255)",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_platform VARCHAR(64)",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS department VARCHAR(255)",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS employment_type VARCHAR(64)",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS workplace_type VARCHAR(64)",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS posted_at TEXT",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS updated_at TEXT",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary VARCHAR(255)",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS experience_years_required REAL",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS education_required VARCHAR(255)",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS responsibilities TEXT DEFAULT '[]'",
                            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS domain VARCHAR(128)",
                            "CREATE INDEX IF NOT EXISTS idx_jobs_platform_ext_id ON jobs(source_platform, external_job_id)",
                        ]:
                            try:
                                await conn.execute(col_stmt)
                            except Exception:
                                pass
                    self._initialized = True
                    logger.info("PostgreSQL database pool initialized successfully with core tables and scraping models.")
                    return
                except Exception as exc:
                    logger.error("Failed to initialize PostgreSQL pool: %s", type(exc).__name__)
                    raise DatabaseError(f"Failed to connect to PostgreSQL database: {type(exc).__name__} - {exc}") from exc

            await asyncio.to_thread(self._sync_init_schema)
            self._initialized = True
            logger.info("SQLite database initialized successfully at '%s'", self._db_path)

    async def get_diagnostic_metadata(self) -> Dict[str, Any]:
        """Execute safe diagnostic query without printing credentials."""
        await self.initialize()
        if self.is_postgres and self._pg_pool:
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT
                        current_database() AS database_name,
                        current_schema() AS schema_name,
                        current_user AS database_user,
                        NOW() AS checked_at;
                """)
                return dict(row) if row else {}
        else:
            return {
                "database_name": self._db_path,
                "schema_name": "main",
                "database_user": "sqlite_user",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    async def check_connectivity(self) -> Dict[str, Any]:
        """Verify database connectivity returning health, dialect, and latency safely."""
        start = asyncio.get_event_loop().time()
        await self.initialize()
        if self.is_postgres and self._pg_pool:
            async with self._pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                latency_ms = (asyncio.get_event_loop().time() - start) * 1000
                return {
                    "status": "connected",
                    "dialect": "PostgreSQL (Neon)",
                    "latency_ms": round(latency_ms, 2),
                    "pool_active": True,
                }
        else:
            row = await self.fetchone("SELECT 1 AS ok")
            latency_ms = (asyncio.get_event_loop().time() - start) * 1000
            return {
                "status": "connected",
                "dialect": "SQLite (Local Dev)",
                "latency_ms": round(latency_ms, 2),
                "db_path": self._db_path,
            }

    async def try_acquire_advisory_lock(self, lock_key: str, timeout_seconds: int = 900) -> bool:
        """Acquire a distributed/advisory lock to prevent overlapping scraping runs."""
        await self.initialize()
        if self.is_postgres and self._pg_pool:
            async with self._pg_pool.acquire() as conn:
                acquired = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", lock_key)
                return bool(acquired)

        # SQLite atomic lock via scraping_locks table with expiration lease
        now_iso = datetime.now(timezone.utc).isoformat()
        expires_iso = (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat()

        def _sync_acquire() -> bool:
            conn = self._get_raw_connection()
            try:
                with conn:
                    # Clear expired lock if present
                    conn.execute("DELETE FROM scraping_locks WHERE lock_name = ? AND expires_at < ?", (lock_key, now_iso))
                    try:
                        conn.execute(
                            "INSERT INTO scraping_locks (lock_name, locked_by, locked_at, expires_at) VALUES (?, ?, ?, ?)",
                            (lock_key, "worker", now_iso, expires_iso),
                        )
                        return True
                    except sqlite3.IntegrityError:
                        return False
            finally:
                conn.close()

        return await asyncio.to_thread(_sync_acquire)

    async def release_advisory_lock(self, lock_key: str) -> bool:
        """Release a previously acquired advisory lock."""
        await self.initialize()
        if self.is_postgres and self._pg_pool:
            async with self._pg_pool.acquire() as conn:
                released = await conn.fetchval("SELECT pg_advisory_unlock(hashtext($1))", lock_key)
                return bool(released)

        def _sync_release() -> bool:
            conn = self._get_raw_connection()
            try:
                with conn:
                    cursor = conn.execute("DELETE FROM scraping_locks WHERE lock_name = ?", (lock_key,))
                    return cursor.rowcount > 0
            finally:
                conn.close()

        return await asyncio.to_thread(_sync_release)

    def _sync_init_schema(self) -> None:
        conn = self._get_raw_connection()
        try:
            with conn:
                conn.executescript(CREATE_USERS_TABLE)
                conn.executescript(CREATE_JOBS_TABLE)
                conn.executescript(CREATE_CANDIDATE_PROFILES_TABLE)
                conn.executescript(CREATE_MATCH_RECORDS_TABLE)
                conn.executescript(CREATE_SCRAPING_RUNS_TABLE)
                conn.executescript(CREATE_SCRAPING_SOURCES_TABLE)
                conn.executescript(CREATE_SCRAPING_LOCKS_TABLE)

                # SQLite migration: add missing columns to jobs table idempotently
                cursor = conn.execute("PRAGMA table_info(jobs)")
                existing_cols = {row["name"] for row in cursor.fetchall()}
                migration_cols = [
                    ("external_job_id", "TEXT"),
                    ("source_platform", "TEXT"),
                    ("department", "TEXT"),
                    ("employment_type", "TEXT"),
                    ("workplace_type", "TEXT"),
                    ("posted_at", "TEXT"),
                    ("updated_at", "TEXT"),
                    ("salary", "TEXT"),
                    ("experience_years_required", "REAL"),
                    ("education_required", "TEXT"),
                    ("responsibilities", "TEXT DEFAULT '[]'"),
                    ("domain", "TEXT"),
                ]
                for col_name, col_type in migration_cols:
                    if col_name not in existing_cols:
                        try:
                            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
                        except Exception:
                            pass
        finally:
            conn.close()

    async def execute(self, query: str, params: Tuple = ()) -> int:
        """Execute non-query command (INSERT, UPDATE, DELETE). Returns affected rows."""
        await self.initialize()
        if self.is_postgres and self._pg_pool:
            pg_query = self._to_pg_query(query)
            async with self._pg_pool.acquire() as conn:
                try:
                    status = await conn.execute(pg_query, *params)
                    parts = status.split()
                    return int(parts[-1]) if parts and parts[-1].isdigit() else 1
                except asyncpg.UniqueViolationError as exc:
                    raise UniqueConstraintViolationError(f"Unique constraint violated: {exc}") from exc
                except Exception as exc:
                    raise DatabaseError(f"PostgreSQL execute error: {exc}") from exc

        return await asyncio.to_thread(self._sync_execute, query, params)

    def _sync_execute(self, query: str, params: Tuple) -> int:
        conn = self._get_raw_connection()
        try:
            with conn:
                cursor = conn.execute(query, params)
                return cursor.rowcount
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise UniqueConstraintViolationError(f"Unique constraint violated: {exc}") from exc
            raise DatabaseError(f"Database integrity error: {exc}") from exc
        except Exception as exc:
            raise DatabaseError(f"Database query execution error: {exc}") from exc
        finally:
            conn.close()

    async def fetchone(self, query: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:
        """Fetch single matching row as a dict."""
        await self.initialize()
        if self.is_postgres and self._pg_pool:
            pg_query = self._to_pg_query(query)
            async with self._pg_pool.acquire() as conn:
                try:
                    row = await conn.fetchrow(pg_query, *params)
                    return dict(row) if row else None
                except Exception as exc:
                    raise DatabaseError(f"PostgreSQL fetchone error: {exc}") from exc

        return await asyncio.to_thread(self._sync_fetchone, query, params)

    def _sync_fetchone(self, query: str, params: Tuple) -> Optional[Dict[str, Any]]:
        conn = self._get_raw_connection()
        try:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    async def fetchall(self, query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        """Fetch all matching rows as a list of dicts."""
        await self.initialize()
        if self.is_postgres and self._pg_pool:
            pg_query = self._to_pg_query(query)
            async with self._pg_pool.acquire() as conn:
                try:
                    rows = await conn.fetch(pg_query, *params)
                    return [dict(r) for r in rows if r]
                except Exception as exc:
                    raise DatabaseError(f"PostgreSQL fetchall error: {exc}") from exc

        return await asyncio.to_thread(self._sync_fetchall, query, params)

    def _sync_fetchall(self, query: str, params: Tuple) -> List[Dict[str, Any]]:
        conn = self._get_raw_connection()
        try:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Union["DatabaseTransaction", "PgDatabaseTransaction"], None]:
        """Context manager providing ACID transaction with automatic commit and rollback."""
        await self.initialize()
        if self.is_postgres and self._pg_pool:
            async with self._pg_pool.acquire() as conn:
                async with conn.transaction():
                    yield PgDatabaseTransaction(conn)
            return

        tx = DatabaseTransaction(self._get_raw_connection())
        try:
            await asyncio.to_thread(tx.begin)
            yield tx
            await asyncio.to_thread(tx.commit)
        except Exception:
            await asyncio.to_thread(tx.rollback)
            raise
        finally:
            await asyncio.to_thread(tx.close)

    async def close(self) -> None:
        """Close database connection pool if PostgreSQL."""
        if self._pg_pool:
            await self._pg_pool.close()
            self._pg_pool = None
            self._initialized = False


class PgDatabaseTransaction:
    """Encapsulates a PostgreSQL transaction via asyncpg."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def execute(self, query: str, params: Tuple = ()) -> int:
        pg_query = DatabaseManager._to_pg_query(query)
        try:
            status = await self._conn.execute(pg_query, *params)
            parts = status.split()
            return int(parts[-1]) if parts and parts[-1].isdigit() else 1
        except asyncpg.UniqueViolationError as exc:
            raise UniqueConstraintViolationError(f"Unique constraint violated: {exc}") from exc
        except Exception as exc:
            raise DatabaseError(f"Integrity violation: {exc}") from exc


class DatabaseTransaction:
    """Encapsulates an isolated SQLite transaction."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE;")

    def commit(self) -> None:
        self._conn.execute("COMMIT;")

    def rollback(self) -> None:
        try:
            self._conn.execute("ROLLBACK;")
        except Exception:
            pass

    def close(self) -> None:
        self._conn.close()

    def execute(self, query: str, params: Tuple = ()) -> int:
        try:
            cursor = self._conn.execute(query, params)
            return cursor.rowcount
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise UniqueConstraintViolationError(f"Unique constraint violated: {exc}") from exc
            raise DatabaseError(f"Integrity violation: {exc}") from exc


_global_db_manager: Optional[DatabaseManager] = None


def get_db_manager(database_url: Optional[str] = None) -> DatabaseManager:
    """Return singleton database manager."""
    global _global_db_manager
    if _global_db_manager is None or (database_url and database_url != _global_db_manager.database_url):
        _global_db_manager = DatabaseManager(database_url=database_url)
    return _global_db_manager

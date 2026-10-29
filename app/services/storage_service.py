"""Persistent file storage abstraction and local filesystem implementation for resumes."""

import os
from pathlib import Path
import re
from typing import Optional
import uuid
import aiofiles

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, NotFoundError


class IFileStorageService:
    """Interface for private resume document storage."""

    async def save_file(self, user_id: str, filename: str, content: bytes) -> str:
        """Store file content and return unique private storage key."""
        raise NotImplementedError

    async def get_file(self, storage_key: str) -> bytes:
        """Retrieve stored file bytes by storage key."""
        raise NotImplementedError

    async def delete_file(self, storage_key: str) -> bool:
        """Safely delete stored file."""
        raise NotImplementedError

    def get_absolute_path(self, storage_key: str) -> Optional[Path]:
        """Return absolute path if local filesystem storage is used."""
        raise NotImplementedError


class LocalFileStorageService(IFileStorageService):
    """Local private filesystem storage implementation with user path isolation."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        settings = get_settings()
        storage_root = base_dir or getattr(settings, "RESUME_STORAGE_DIR", "storage/resumes")
        self.base_path = Path(storage_root).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to prevent directory traversal and invalid characters."""
        base = os.path.basename(filename)
        cleaned = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", base)
        return cleaned or "document.pdf"

    def _resolve_safe_path(self, storage_key: str) -> Path:
        """Validate and resolve storage key strictly within base directory."""
        if not storage_key or ".." in storage_key or storage_key.startswith("/"):
            raise BadRequestError("Invalid storage key path.")
        
        target = (self.base_path / storage_key).resolve()
        if not str(target).startswith(str(self.base_path)):
            raise BadRequestError("Path traversal attempt detected in storage key.")
        return target

    async def save_file(self, user_id: str, filename: str, content: bytes) -> str:
        """Store file under private user directory and return relative storage key."""
        clean_name = self._sanitize_filename(filename)
        clean_user = re.sub(r"[^a-zA-Z0-9_\-]", "_", user_id)
        unique_prefix = uuid.uuid4().hex[:12]
        relative_key = f"{clean_user}/{unique_prefix}_{clean_name}"

        destination = self.base_path / clean_user / f"{unique_prefix}_{clean_name}"
        destination.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(destination, "wb") as f:
            await f.write(content)

        return relative_key

    async def get_file(self, storage_key: str) -> bytes:
        """Read and return file bytes."""
        path = self._resolve_safe_path(storage_key)
        if not path.exists() or not path.is_file():
            raise NotFoundError(f"Stored file for key '{storage_key}' not found.")
        
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def delete_file(self, storage_key: str) -> bool:
        """Delete stored file if present."""
        try:
            path = self._resolve_safe_path(storage_key)
            if path.exists() and path.is_file():
                path.unlink()
                # Clean up empty parent folder if empty
                try:
                    parent = path.parent
                    if parent != self.base_path and not any(parent.iterdir()):
                        parent.rmdir()
                except Exception:
                    pass
                return True
        except Exception:
            return False
        return False

    def get_absolute_path(self, storage_key: str) -> Optional[Path]:
        """Return validated Path object."""
        try:
            return self._resolve_safe_path(storage_key)
        except Exception:
            return None


_storage_service: Optional[IFileStorageService] = None


def get_storage_service() -> IFileStorageService:
    """Dependency provider returning singleton storage service instance."""
    global _storage_service
    if _storage_service is None:
        _storage_service = LocalFileStorageService()
    return _storage_service

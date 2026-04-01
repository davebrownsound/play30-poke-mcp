"""
JSON file-based AsyncKeyValue storage for FastMCP OAuth.

Persists client registrations, tokens, and OAuth state to disk so they
survive Render redeploys on free tier.
"""

import json
import os
import time
import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, SupportsFloat


DATA_DIR = os.environ.get("STORAGE_DIR", "/tmp/play30-oauth-data")


class FileKeyValue:
    """Simple JSON-file-backed key-value store implementing AsyncKeyValue protocol."""

    def __init__(self, base_dir: str = DATA_DIR):
        self._base_dir = base_dir
        self._lock = asyncio.Lock()
        os.makedirs(base_dir, exist_ok=True)

    def _collection_dir(self, collection: str | None) -> str:
        coll = collection or "_default"
        path = os.path.join(self._base_dir, coll)
        os.makedirs(path, exist_ok=True)
        return path

    def _key_path(self, key: str, collection: str | None) -> str:
        # Sanitize key for filesystem
        safe_key = key.replace("/", "_").replace("\\", "_")
        return os.path.join(self._collection_dir(collection), f"{safe_key}.json")

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        path = self._key_path(key, collection)
        if not os.path.exists(path):
            return None

        async with self._lock:
            try:
                with open(path, "r") as f:
                    record = json.load(f)
            except (json.JSONDecodeError, OSError):
                return None

        # Check TTL expiry
        expires_at = record.get("_expires_at")
        if expires_at is not None and time.time() > expires_at:
            await self.delete(key, collection=collection)
            return None

        return record.get("_value")

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        path = self._key_path(key, collection)
        record: dict[str, Any] = {"_value": dict(value)}
        if ttl is not None:
            record["_expires_at"] = time.time() + float(ttl)

        async with self._lock:
            with open(path, "w") as f:
                json.dump(record, f)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        path = self._key_path(key, collection)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        return [await self.get(k, collection=collection) for k in keys]

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        for k, v in zip(keys, values):
            await self.put(k, v, collection=collection, ttl=ttl)

    async def delete_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> int:
        count = 0
        for k in keys:
            if await self.delete(k, collection=collection):
                count += 1
        return count

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        path = self._key_path(key, collection)
        if not os.path.exists(path):
            return None, None

        async with self._lock:
            try:
                with open(path, "r") as f:
                    record = json.load(f)
            except (json.JSONDecodeError, OSError):
                return None, None

        expires_at = record.get("_expires_at")
        value = record.get("_value")

        if expires_at is not None:
            remaining = expires_at - time.time()
            if remaining <= 0:
                await self.delete(key, collection=collection)
                return None, None
            return value, remaining

        return value, None

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [await self.ttl(k, collection=collection) for k in keys]

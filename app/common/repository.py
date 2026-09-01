"""Backward-compatible exports for the tenant-safe repository layer."""

from app.repositories.mongo import BaseRepository, TenantRepository

__all__ = ["BaseRepository", "TenantRepository"]

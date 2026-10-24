"""Limpieza periódica: temporales caducados y proyectos inactivos."""
from __future__ import annotations

import logging
import shutil
import time

from .. import db, storage
from ..config import settings

log = logging.getLogger("maintenance")


def cleanup() -> dict[str, int]:
    removed_tmp = 0
    limit = time.time() - settings.temp_retention_hours * 3600
    if settings.temp_dir.exists():
        for p in settings.temp_dir.iterdir():
            try:
                if p.stat().st_mtime < limit:
                    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
                    removed_tmp += 1
            except FileNotFoundError:
                pass
    expired = db.all_("SELECT id FROM assets WHERE expires_at IS NOT NULL AND expires_at<?", (time.time(),))
    for a in expired:
        storage.delete_asset(a["id"])
    removed_projects = 0
    if settings.retention_days > 0:
        old = db.all_("SELECT id, user_id FROM projects WHERE updated_at<?",
                      (time.time() - settings.retention_days * 86400,))
        for p in old:
            storage.delete_project_files(p["user_id"], p["id"])
            db.execute("DELETE FROM projects WHERE id=?", (p["id"],))
            db.audit("proyecto_eliminado_por_caducidad", p["user_id"], p["id"])
            removed_projects += 1
    db.execute("DELETE FROM sessions WHERE expires_at<?", (time.time(),))
    db.execute("UPDATE consents SET status='expired' WHERE status='pending' AND expires_at<?", (time.time(),))
    return {"temp": removed_tmp, "assets": len(expired), "projects": removed_projects}

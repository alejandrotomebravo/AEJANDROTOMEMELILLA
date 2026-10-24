"""Cola de trabajos: reintentos, errores definitivos, cancelación y recuperación."""
from __future__ import annotations

from app import db
from app.errors import QualityError, RetryableError
from app.jobs import queue


def _user():
    uid = db.new_id()
    db.execute("INSERT INTO users(id,email,pw_hash,created_at) VALUES (?,?,?,?)", (uid, f"{uid}@x.es", "x", db.now()))
    return uid


def test_retryable_errors_are_retried_then_fail(env, monkeypatch):
    calls = {"n": 0}

    @queue.handler("prueba_transitoria")
    def _h(ctx):
        calls["n"] += 1
        ctx.checkpoint["intento"] = calls["n"]
        raise RetryableError("servicio caído")

    uid = _user()
    j = queue.enqueue("prueba_transitoria", uid, None, {}, max_attempts=3)
    for _ in range(3):
        db.execute("UPDATE jobs SET next_run_at=0 WHERE id=?", (j["id"],))
        queue.run_pending(1)
    final = queue.get(j["id"])
    assert calls["n"] == 3 and final["status"] == "failed"
    assert db.jload(db.one("SELECT checkpoint FROM jobs WHERE id=?", (j["id"],))["checkpoint"])["intento"] == 3


def test_quality_errors_fail_without_retry(env):
    @queue.handler("prueba_calidad")
    def _h(ctx):
        raise QualityError("rostro demasiado pequeño", details={"report": {"ok": False}})

    j = queue.enqueue("prueba_calidad", _user(), None, {})
    queue.run_pending()
    final = queue.get(j["id"])
    assert final["status"] == "failed" and final["attempts"] == 1
    assert final["error_code"] == "calidad_insuficiente" and final["result"]["details"]["report"] == {"ok": False}


def test_unexpected_errors_are_sanitized(env):
    @queue.handler("prueba_fallo")
    def _h(ctx):
        raise RuntimeError("fallo en /home/ana/secreto/video.mp4 de ana@correo.es")

    j = queue.enqueue("prueba_fallo", _user(), None, {}, max_attempts=1)
    queue.run_pending()
    final = queue.get(j["id"])
    assert final["status"] == "failed" and "ana" not in (final["error"] or "")
    assert queue.sanitize("ver /home/ana/x.mp4 o ana@x.es") == "ver [ruta] o [correo]"


def test_cancel_queued_and_running(env):
    @queue.handler("prueba_cancelar")
    def _h(ctx):
        db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (ctx.id,))
        ctx.check_cancel()

    uid = _user()
    j1 = queue.enqueue("prueba_cancelar", uid, None, {})
    queue.request_cancel(j1["id"])
    assert queue.get(j1["id"])["status"] == "cancelled"
    j2 = queue.enqueue("prueba_cancelar", uid, None, {})
    queue.run_pending()
    assert queue.get(j2["id"])["status"] == "cancelled"


def test_stale_running_jobs_are_recovered(env):
    j = queue.enqueue("prueba_x", _user(), None, {})
    db.execute("UPDATE jobs SET status='running', heartbeat_at=? WHERE id=?", (db.now() - 99999, j["id"]))
    assert queue.recover_stale() == 1
    assert queue.get(j["id"])["status"] == "queued"


def test_manual_retry(env):
    @queue.handler("prueba_reintento")
    def _h(ctx):
        if ctx.params.get("fallar") and ctx.attempt == 1 and not ctx.checkpoint.get("ok"):
            ctx.checkpoint["ok"] = True
            ctx.save_checkpoint()
            raise QualityError("no")
        return {"hecho": True}

    j = queue.enqueue("prueba_reintento", _user(), None, {"fallar": True})
    queue.run_pending()
    assert queue.get(j["id"])["status"] == "failed"
    queue.retry(j["id"])
    queue.run_pending()
    assert queue.get(j["id"])["result"] == {"hecho": True}

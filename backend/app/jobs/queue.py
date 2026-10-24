"""Cola de trabajos persistente sobre SQLite.

* Los trabajos sobreviven a reinicios: los que quedaron «en curso» sin latido
  reciente se devuelven a la cola (recuperación ante caídas).
* Reintentos controlados con espera exponencial sólo para errores
  transitorios (``RetryableError`` o fallos inesperados); los errores de
  validación, calidad o consentimiento fallan sin reintentar.
* Puntos de control (``checkpoint``) para reanudar trabajos por fragmentos.
* El registro de errores guarda la clase y un mensaje saneado, nunca rutas ni
  datos personales.
"""
from __future__ import annotations

import logging
import re
import socket
import threading
import time
import traceback
from typing import Any, Callable

from .. import db
from ..config import settings
from ..errors import AppError, JobCancelled

log = logging.getLogger("jobs")

Handler = Callable[["JobContext"], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}
WORKER_ID = f"{socket.gethostname()}:{threading.get_ident()}"


def handler(name: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn
    return deco


def enqueue(type_: str, user_id: str, project_id: str | None, params: dict[str, Any],
            max_attempts: int | None = None, message: str = "En cola") -> dict[str, Any]:
    jid = db.new_id()
    db.execute(
        """INSERT INTO jobs(id,user_id,project_id,type,status,params,max_attempts,message,created_at,next_run_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (jid, user_id, project_id, type_, "queued", db.jdump(params),
         max_attempts or settings.job_max_attempts, message, db.now(), 0),
    )
    return get(jid)


def get(job_id: str) -> dict[str, Any]:
    j = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not j:
        from ..errors import NotFound
        raise NotFound("Trabajo no encontrado.")
    return public(j)


PRIVATE_PARAMS = {"file_url"}  # enlaces de descarga (pueden ser temporales o ligados a un token)


def public(j: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": j["id"], "type": j["type"], "status": j["status"], "progress": round(j["progress"], 3),
        "message": j["message"], "project_id": j["project_id"],
        "params": {k: v for k, v in db.jload(j["params"], {}).items() if k not in PRIVATE_PARAMS}, "result": db.jload(j["result"], None),
        "error": j["error"], "error_code": j["error_code"], "attempts": j["attempts"],
        "max_attempts": j["max_attempts"], "created_at": j["created_at"], "started_at": j["started_at"],
        "finished_at": j["finished_at"], "user_id": j["user_id"],
    }


def claim() -> dict[str, Any] | None:
    with db.tx() as c:
        row = c.execute(
            "SELECT * FROM jobs WHERE status='queued' AND next_run_at<=? ORDER BY created_at LIMIT 1",
            (db.now(),)).fetchone()
        if not row:
            return None
        c.execute("""UPDATE jobs SET status='running', attempts=attempts+1, locked_by=?, heartbeat_at=?,
                     started_at=COALESCE(started_at, ?), message='Procesando' WHERE id=?""",
                  (WORKER_ID, db.now(), db.now(), row["id"]))
        return dict(row) | {"attempts": row["attempts"] + 1}


def recover_stale() -> int:
    limit = db.now() - settings.job_stale_seconds
    cur = db.execute("""UPDATE jobs SET status='queued', locked_by=NULL, message='Reanudando tras una interrupción'
                        WHERE status='running' AND (heartbeat_at IS NULL OR heartbeat_at<?)""", (limit,))
    return cur.rowcount


def request_cancel(job_id: str) -> None:
    db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
    db.execute("""UPDATE jobs SET status='cancelled', finished_at=?, message='Cancelado'
                  WHERE id=? AND status='queued'""", (db.now(), job_id))


def retry(job_id: str) -> None:
    db.execute("""UPDATE jobs SET status='queued', cancel_requested=0, error=NULL, error_code=NULL,
                  attempts=0, next_run_at=0, finished_at=NULL, message='En cola (reintento manual)'
                  WHERE id=? AND status IN ('failed','cancelled')""", (job_id,))


_PATH_RE = re.compile(r"(/[\w.\-]+){2,}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def sanitize(msg: str) -> str:
    msg = _PATH_RE.sub("[ruta]", msg)
    return _EMAIL_RE.sub("[correo]", msg)[:600]


class JobContext:
    def __init__(self, row: dict[str, Any]):
        self.id = row["id"]
        self.user_id = row["user_id"]
        self.project_id = row["project_id"]
        self.type = row["type"]
        self.params: dict[str, Any] = db.jload(row["params"], {})
        self.checkpoint: dict[str, Any] = db.jload(row["checkpoint"], {})
        self.attempt = row["attempts"]
        self._last_beat = 0.0
        self._phase = (0.0, 1.0)

    def phase(self, start: float, end: float, message: str | None = None) -> None:
        """Define el tramo [start, end] de la barra de progreso para la siguiente etapa."""
        self._phase = (start, end)
        self.progress(0.0, message)

    def progress(self, fraction: float, message: str | None = None) -> None:
        a, b = self._phase
        value = a + (b - a) * max(0.0, min(1.0, fraction))
        t = time.time()
        if message is None and t - self._last_beat < 0.5:
            return
        self._last_beat = t
        if message:
            db.execute("UPDATE jobs SET progress=?, heartbeat_at=?, message=? WHERE id=?",
                       (value, t, message, self.id))
        else:
            db.execute("UPDATE jobs SET progress=?, heartbeat_at=? WHERE id=?", (value, t, self.id))

    def cancelled(self) -> bool:
        row = db.one("SELECT cancel_requested FROM jobs WHERE id=?", (self.id,))
        return bool(row and row["cancel_requested"])

    def check_cancel(self) -> None:
        if self.cancelled():
            raise JobCancelled()

    def save_checkpoint(self) -> None:
        db.execute("UPDATE jobs SET checkpoint=?, heartbeat_at=? WHERE id=?",
                   (db.jdump(self.checkpoint), db.now(), self.id))


def run_job(row: dict[str, Any]) -> None:
    ctx = JobContext(row)
    fn = HANDLERS.get(row["type"])
    try:
        if fn is None:
            raise AppError(f"Tipo de trabajo desconocido: {row['type']}")
        result = fn(ctx) or {}
        db.execute("""UPDATE jobs SET status='succeeded', progress=1, result=?, finished_at=?, message='Completado',
                      error=NULL, error_code=NULL, checkpoint=? WHERE id=?""",
                   (db.jdump(result), db.now(), db.jdump(ctx.checkpoint), ctx.id))
    except JobCancelled:
        db.execute("UPDATE jobs SET status='cancelled', finished_at=?, message='Cancelado' WHERE id=?",
                   (db.now(), ctx.id))
    except Exception as exc:  # noqa: BLE001
        app_err = isinstance(exc, AppError)
        retryable = (not app_err) or getattr(exc, "retryable", False)
        msg = exc.message if app_err else "Error interno durante el procesamiento."
        code = exc.code if app_err else "error_interno"
        log.error("job %s (%s) intento %s: %s %s", ctx.id, ctx.type, ctx.attempt, type(exc).__name__,
                  sanitize(str(exc)))
        if not app_err:
            log.debug(sanitize(traceback.format_exc()))
        details = exc.details if app_err else {}
        if retryable and ctx.attempt < row["max_attempts"]:
            delay = min(300, 5 * 2 ** (ctx.attempt - 1))
            db.execute("""UPDATE jobs SET status='queued', next_run_at=?, message=?, error=?, error_code=?,
                          checkpoint=? WHERE id=?""",
                       (db.now() + delay, f"Reintentando en {delay} s (intento {ctx.attempt + 1})",
                        sanitize(msg), code, db.jdump(ctx.checkpoint), ctx.id))
        else:
            db.execute("""UPDATE jobs SET status='failed', finished_at=?, message='Error', error=?, error_code=?,
                          result=?, checkpoint=? WHERE id=?""",
                       (db.now(), sanitize(msg), code, db.jdump({"details": details}) if details else None,
                        db.jdump(ctx.checkpoint), ctx.id))


def run_pending(max_jobs: int | None = None) -> int:
    """Procesa trabajos en el hilo actual (usado por las pruebas y por el modo CLI)."""
    n = 0
    while max_jobs is None or n < max_jobs:
        row = claim()
        if not row:
            break
        run_job(row)
        n += 1
    return n


class Worker:
    def __init__(self, concurrency: int = 1, poll: float = 1.0):
        self.concurrency = concurrency
        self.poll = poll
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_cleanup = 0.0

    def start(self) -> None:
        recover_stale()
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)

    def _loop(self) -> None:
        from . import maintenance
        while not self._stop.is_set():
            try:
                if time.time() - self._last_cleanup > 3600:
                    self._last_cleanup = time.time()
                    recover_stale()
                    maintenance.cleanup()
                row = claim()
                if row:
                    run_job(row)
                    continue
            except Exception:  # noqa: BLE001
                log.exception("Fallo en el bucle del trabajador")
            self._stop.wait(self.poll)

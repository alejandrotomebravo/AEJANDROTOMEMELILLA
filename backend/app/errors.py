"""Errores de dominio con mensajes en español para el usuario final."""
from __future__ import annotations


class AppError(Exception):
    status = 400
    code = "error"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None,
                 details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.details = details or {}


class NotFound(AppError):
    status = 404
    code = "no_encontrado"


class Forbidden(AppError):
    status = 403
    code = "prohibido"


class ConsentError(AppError):
    status = 403
    code = "consentimiento"


class PolicyError(AppError):
    status = 422
    code = "politica_de_uso"


class QualityError(AppError):
    """El material de entrada no permite un resultado aceptable."""
    status = 422
    code = "calidad_insuficiente"


class ProviderUnavailable(AppError):
    status = 503
    code = "proveedor_no_disponible"


class LimitExceeded(AppError):
    status = 413
    code = "limite_superado"


class Conflict(AppError):
    status = 409
    code = "conflicto"


class RetryableError(AppError):
    """Error transitorio: la cola reintentará el trabajo."""
    status = 503
    code = "error_transitorio"
    retryable = True


class JobCancelled(Exception):
    pass

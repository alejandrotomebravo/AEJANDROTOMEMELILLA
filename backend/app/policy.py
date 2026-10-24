"""Reglas de uso: procedencia del material, declaración de uso, consentimiento
y filtrado básico de diálogos.

Estas comprobaciones son medidas técnicas de diligencia; no sustituyen un
análisis jurídico ni pueden verificar por sí solas la veracidad de lo
declarado. Toda declaración queda registrada en la tabla de auditoría.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .config import settings
from .errors import ConsentError, PolicyError

PURPOSES = {
    "parodia": "Parodia o humor",
    "ficcion": "Ficción o creación artística",
    "doblaje": "Doblaje o traducción",
    "educativo": "Educativo o divulgativo",
    "personal": "Uso personal o familiar",
    "publicidad": "Publicidad o uso comercial",
}

REQUIRED_CONFIRMATIONS = {
    "no_intimo": "No crearé contenido sexual o íntimo de ninguna persona.",
    "no_fraude": "No usaré el vídeo para suplantar identidades, estafar ni obtener datos o dinero.",
    "no_acoso": "No usaré el vídeo para acosar, humillar o dañar la reputación de nadie.",
    "no_pruebas": "No presentaré el vídeo como prueba, documento o noticia real.",
    "no_desinformacion": "No usaré el vídeo para engañar sobre hechos reales, procesos electorales o "
                         "declaraciones de personas reales.",
    "no_famosos": "Sólo crearé personajes de personas que me lo hayan autorizado (no de famosos ni de terceros "
                  "sin su consentimiento).",
    "mantener_aviso": "No eliminaré ni ocultaré la marca de agua ni el aviso de contenido sintético.",
}


def validate_use_declaration(d: dict[str, Any]) -> dict[str, Any]:
    purpose = d.get("purpose")
    if purpose not in PURPOSES:
        raise PolicyError("Seleccione la finalidad del vídeo.")
    conf = d.get("confirmations") or {}
    missing = [txt for key, txt in REQUIRED_CONFIRMATIONS.items() if not conf.get(key)]
    if missing:
        raise PolicyError("Debe aceptar todos los compromisos de uso responsable.", details={"pendientes": missing})
    warnings = []
    return {"purpose": purpose, "detail": (d.get("detail") or "").strip()[:500],
            "distribution": d.get("distribution") if d.get("distribution") in ("privado", "redes", "comercial")
            else "privado",
            "confirmations": {k: True for k in REQUIRED_CONFIRMATIONS}, "warnings": warnings}


# ----------------------------------------------------------- texto del diálogo

_BLOCK_PATTERNS = [
    (r"\b(desnud[oa]s?|porn\w*|sexo|sexual\w*|follar|masturb\w*|orgasm\w*|genital\w*)\b",
     "contenido sexual"),
    (r"\b(transfi[eé]r\w*|ingres\w*|env[ií]\w*|haz(me)?)\b.{0,40}\b(dinero|euros|€|bizum|cuenta|tarjeta)\b",
     "solicitud de dinero o transferencias"),
    (r"\b(contrase[nñ]a|c[oó]digo de verificaci[oó]n|pin de (la )?tarjeta|n[uú]mero de (la )?tarjeta|cvv|iban)\b",
     "solicitud de credenciales o datos bancarios"),
    (r"\b(te (voy a|vamos a) matar|te mato|mátate|suic[ií]date)\b", "amenazas o incitación a autolesiones"),
]


def check_instructions(text: str) -> None:
    """Filtro previo de las instrucciones del vídeo (además de los filtros de la IA y del proveedor)."""
    check_dialogue_text(text)
    low = text.lower()
    if re.search(r"\b(menor(es)? de edad|niñ[oa]s?|bebés?)\b.{0,60}\b(desnud|beso|sex|bañ)", low):
        raise PolicyError("Las instrucciones combinan menores con situaciones no permitidas.", code="instrucciones_rechazadas")
    if re.search(r"\b(noticia real|declaraciones? reales?|como si fuera real|que parezca real|para que crean)\b", low):
        raise PolicyError("No se pueden crear vídeos para hacerlos pasar por reales. Indique que es ficción o parodia.",
                          code="instrucciones_rechazadas")


def check_dialogue_text(text: str) -> None:
    """Filtro básico por patrones. Puede dar falsos positivos y no detecta todo:
    es una primera barrera, complementaria a la declaración de uso."""
    low = text.lower()
    for pattern, label in _BLOCK_PATTERNS:
        if re.search(pattern, low):
            raise PolicyError(
                f"El diálogo parece incluir {label}, que no está permitido en esta aplicación. "
                "Si cree que se trata de un error, reformule el texto.", code="dialogo_bloqueado")


# ----------------------------------------------------------- consentimiento

def require_identity_usable(identity: dict[str, Any], scope: str) -> None:
    """Lanza ConsentError si la identidad no puede usarse para ``scope`` (imagen|voz)."""
    if identity.get("revoked_at") or identity["consent_status"] == "revoked":
        raise ConsentError(f"La persona «{identity['display_name']}» ha retirado su consentimiento.")
    if identity["is_minor"] and not settings.allow_minors:
        raise ConsentError("Por protección de menores, este servidor no permite utilizar la imagen ni la voz "
                           "de personas menores de edad.")
    if identity["consent_status"] not in ("verified_self", "verified"):
        raise ConsentError(f"La identidad «{identity['display_name']}» no tiene un consentimiento verificado.")
    scopes = identity["scopes"] if isinstance(identity["scopes"], list) else []
    if scope not in scopes:
        raise ConsentError(f"El consentimiento de «{identity['display_name']}» no cubre el uso de su {scope}.")


CONSENT_STATEMENT_VERSION = "2026-10-v2"


def consent_statement(identity_name: str, scopes: list[str], purpose: str, requester: str,
                      guardian: bool = False) -> str:
    uso = " y ".join({"imagen": "mi imagen (rostro), también en forma de caricatura o personaje ilustrado",
                      "voz": "mi voz"}[s] for s in scopes)
    if guardian:
        uso = uso.replace("mi imagen", "la imagen del menor").replace("mi voz", "la voz del menor")
    return (
        f"Yo, en calidad de {'representante legal de ' + identity_name if guardian else identity_name}, "
        f"autorizo a {requester} a utilizar {uso} para crear vídeos generados con inteligencia artificial "
        f"con la siguiente finalidad: «{purpose}». Entiendo que los vídeos llevarán un aviso visible de "
        f"contenido sintético, que no podrán utilizarse con fines sexuales, fraudulentos, de acoso ni para "
        f"engañar sobre hechos reales, y que puedo retirar este consentimiento en cualquier momento desde "
        f"este mismo enlace, lo que impedirá nuevos procesamientos y exportaciones con mi {('imagen' if 'imagen' in scopes else 'voz')}. "
        f"Versión del texto: {CONSENT_STATEMENT_VERSION}."
    )

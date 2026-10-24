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

SOURCE_TYPES = {
    "propio": "Grabado por mí o por mi organización",
    "licencia": "Material con licencia que permite su modificación",
    "autorizado": "Material de un tercero que me ha autorizado expresamente",
    "dominio_publico_cc": "Dominio público o licencia Creative Commons que permite obras derivadas",
}

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
    "mantener_aviso": "No eliminaré ni ocultaré la marca de agua ni el aviso de contenido sintético.",
}


def validate_provenance(p: dict[str, Any], origin: dict[str, Any] | None = None) -> dict[str, Any]:
    """Valida la declaración de derechos sobre una fuente.

    Distingue tres permisos: acceder/descargar (lo da la fuente), modificar y
    redistribuir el resultado (los declara el usuario), y usar la imagen/voz de
    quienes aparecen (consentimiento de esas personas)."""
    origin = origin or {}
    st = p.get("source_type")
    if st not in SOURCE_TYPES:
        raise PolicyError("Indique la procedencia del vídeo.")
    if not p.get("attested"):
        raise PolicyError("Debe declarar que dispone de los derechos necesarios sobre el vídeo.")
    if not p.get("can_modify"):
        raise PolicyError("Confirme que tiene permiso para MODIFICAR el vídeo: poder verlo o descargarlo no lo implica.")
    warnings: list[str] = []
    checks: list[str] = []
    lic = origin.get("license") or {}
    if origin.get("connector"):
        p = {**p, "internet_download": True}
        if not p.get("license_url") and lic.get("url"):
            p["license_url"] = lic["url"]
        if not p.get("rights_holder") and origin.get("author"):
            p["rights_holder"] = origin["author"]
        if lic.get("allows_modification") is False and st != "autorizado":
            raise PolicyError(f"La licencia de la fuente ({lic.get('name')}) no permite modificar el vídeo. Sólo puede "
                              "usarlo con una autorización expresa del titular (opción «autorizado»).")
        if lic.get("code") not in (None, "unknown"):
            checks.append(f"Licencia indicada por {origin.get('platform', 'la fuente')}: {lic.get('name')}.")
        warnings += [n for n in lic.get("notes") or []]
        if lic.get("allows_commercial") is False:
            warnings.append("Licencia no comercial: el resultado no podrá usarse con fines comerciales.")
    if not p.get("can_distribute"):
        warnings.append("No ha confirmado permiso para difundir el resultado: úselo sólo de forma privada.")
    if p.get("internet_download") and st not in ("licencia", "dominio_publico_cc", "autorizado"):
        raise PolicyError(
            "Que un vídeo esté disponible en Internet no le otorga derecho a modificarlo ni a "
            "redistribuirlo. Indique la licencia o la autorización que le permite hacerlo."
        )
    url = (p.get("license_url") or "").strip()
    if st in ("licencia", "dominio_publico_cc", "autorizado"):
        if not url and not p.get("evidence_asset_id") and not (p.get("rights_holder") or "").strip():
            raise PolicyError("Indique el titular de los derechos y aporte el enlace a la licencia o el "
                              "documento de autorización.")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise PolicyError("El enlace de la licencia no es una URL válida.")
        if "creativecommons.org" in parsed.netloc:
            path = parsed.path.lower()
            if "-nd" in path or "/nd" in path:
                raise PolicyError("La licencia Creative Commons indicada incluye la cláusula «Sin obras "
                                  "derivadas» (ND): no permite modificar el vídeo.")
            if "-nc" in path or "/nc" in path:
                warnings.append("La licencia es «No comercial» (NC): el resultado no podrá usarse con fines "
                                "comerciales.")
            if "/publicdomain/" in path or "/licenses/by" in path:
                checks.append("Licencia Creative Commons reconocida en el enlace.")
            if "-sa" in path:
                warnings.append("La licencia exige compartir la obra derivada con la misma licencia (SA).")
            warnings.append("Recuerde citar la autoría tal como exige la licencia.")
    if p.get("third_party_people") and not p.get("third_party_people_consent"):
        raise PolicyError("En el vídeo aparecen otras personas: debe confirmar que cuenta con su autorización "
                          "para alterar su imagen o su voz.")
    clean = {
        "source_type": st,
        "description": (p.get("description") or "").strip()[:500],
        "rights_holder": (p.get("rights_holder") or "").strip()[:200],
        "license_url": url[:500],
        "evidence_asset_id": p.get("evidence_asset_id"),
        "internet_download": bool(p.get("internet_download")),
        "third_party_people": bool(p.get("third_party_people")),
        "third_party_people_consent": bool(p.get("third_party_people_consent")),
        "can_modify": True,
        "can_distribute": bool(p.get("can_distribute")),
        "attested": True,
        "automatic_checks": checks,
        "warnings": warnings,
    }
    return clean


def validate_use_declaration(d: dict[str, Any]) -> dict[str, Any]:
    purpose = d.get("purpose")
    if purpose not in PURPOSES:
        raise PolicyError("Seleccione la finalidad del vídeo.")
    conf = d.get("confirmations") or {}
    missing = [txt for key, txt in REQUIRED_CONFIRMATIONS.items() if not conf.get(key)]
    if missing:
        raise PolicyError("Debe aceptar todos los compromisos de uso responsable.", details={"pendientes": missing})
    warnings = []
    if purpose == "publicidad" and settings.faceswap_provider == "insightface":
        warnings.append("El motor de sustitución facial configurado (InsightFace) sólo permite uso no comercial "
                        "salvo licencia expresa de InsightFace.")
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


CONSENT_STATEMENT_VERSION = "2026-10-v1"


def consent_statement(identity_name: str, scopes: list[str], purpose: str, requester: str,
                      guardian: bool = False) -> str:
    uso = " y ".join({"imagen": "mi imagen (rostro)", "voz": "mi voz"}[s] for s in scopes)
    if guardian:
        uso = uso.replace("mi imagen", "la imagen del menor").replace("mi voz", "la voz del menor")
    return (
        f"Yo, en calidad de {'representante legal de ' + identity_name if guardian else identity_name}, "
        f"autorizo a {requester} a utilizar {uso} para crear vídeos editados con inteligencia artificial "
        f"con la siguiente finalidad: «{purpose}». Entiendo que los vídeos llevarán un aviso visible de "
        f"contenido sintético, que no podrán utilizarse con fines sexuales, fraudulentos, de acoso ni para "
        f"engañar sobre hechos reales, y que puedo retirar este consentimiento en cualquier momento desde "
        f"este mismo enlace, lo que impedirá nuevos procesamientos y exportaciones con mi {('imagen' if 'imagen' in scopes else 'voz')}. "
        f"Versión del texto: {CONSENT_STATEMENT_VERSION}."
    )

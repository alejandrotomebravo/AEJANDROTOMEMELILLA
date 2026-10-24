"""Pruebas unitarias de reglas y utilidades."""
from __future__ import annotations

import pytest

from app.ai import quality, tts
from app.ai.face_detect import Detection, Track, associate, interpolate
from app.errors import ConsentError, PolicyError
from app.media import subtitles, timeline, watermark
from app import policy


# ------------------------------------------------------------ marca de agua

def test_watermark_cannot_be_disabled(env):
    for bad in ({"enabled": False}, {"hidden": True}, {"text": "otra cosa"}, {"base_text": ""}):
        with pytest.raises(PolicyError):
            watermark.validate(bad)


def test_watermark_clamps_size_and_opacity(env):
    cfg = watermark.validate({"size": 0.001, "opacity": 0.05, "labels": ["PARODIA"], "custom": "Aviso"})
    assert cfg["size"] == watermark.MIN_SIZE
    assert cfg["opacity"] == watermark.MIN_OPACITY
    assert watermark.lines(cfg) == [watermark.BASE_TEXT, "PARODIA · Aviso"]


def test_watermark_rejects_unknown_labels_and_long_custom(env):
    with pytest.raises(PolicyError):
        watermark.validate({"labels": ["REAL"]})
    with pytest.raises(PolicyError):
        watermark.validate({"custom": "x" * 200})


# ------------------------------------------------------------ política de uso

def _prov(**kw):
    base = {"source_type": "propio", "attested": True}
    base.update(kw)
    return base


def test_internet_download_requires_license(env):
    with pytest.raises(PolicyError, match="Internet"):
        policy.validate_provenance(_prov(internet_download=True))


def test_creative_commons_nd_is_rejected(env):
    with pytest.raises(PolicyError, match="ND"):
        policy.validate_provenance(_prov(source_type="dominio_publico_cc",
                                         license_url="https://creativecommons.org/licenses/by-nd/4.0/"))
    ok = policy.validate_provenance(_prov(source_type="dominio_publico_cc",
                                          license_url="https://creativecommons.org/licenses/by-nc/4.0/"))
    assert any("No comercial" in w for w in ok["warnings"])


def test_third_party_people_need_consent(env):
    with pytest.raises(PolicyError):
        policy.validate_provenance(_prov(third_party_people=True))


def test_use_declaration_requires_all_commitments(env):
    with pytest.raises(PolicyError):
        policy.validate_use_declaration({"purpose": "parodia", "confirmations": {"no_intimo": True}})
    ok = policy.validate_use_declaration({"purpose": "parodia",
                                          "confirmations": {k: True for k in policy.REQUIRED_CONFIRMATIONS}})
    assert ok["purpose"] == "parodia"


@pytest.mark.parametrize("text", [
    "Hazme un bizum de 50 euros ahora",
    "Dime tu contraseña del banco",
    "Escena de sexo explícito",
])
def test_dialogue_filter_blocks(text, env):
    with pytest.raises(PolicyError):
        policy.check_dialogue_text(text)


def test_dialogue_filter_allows_normal_text(env):
    policy.check_dialogue_text("¡Hola! Bienvenidos a Melilla, hoy os enseño la feria.")


def test_minor_identity_blocked_by_default(env):
    ident = {"display_name": "Niño", "is_minor": True, "consent_status": "verified", "scopes": ["imagen"],
             "revoked_at": None}
    with pytest.raises(ConsentError, match="menores"):
        policy.require_identity_usable(ident, "imagen")


def test_identity_scope_enforced(env):
    ident = {"display_name": "Eva", "is_minor": False, "consent_status": "verified", "scopes": ["imagen"],
             "revoked_at": None}
    policy.require_identity_usable(ident, "imagen")
    with pytest.raises(ConsentError):
        policy.require_identity_usable(ident, "voz")


# ------------------------------------------------------------ línea de tiempo

def test_source_to_output_mapping():
    tl = timeline.empty(10)
    tl["video"]["clips"] = [{"id": "a", "in": 5, "out": 8}, {"id": "b", "in": 0, "out": 2}]
    assert timeline.output_duration(tl) == 5
    assert timeline.source_to_output(tl, 6, 7) == [(1.0, 2.0)]
    assert timeline.source_to_output(tl, 1, 6) == [(0.0, 1.0), (4.0, 5.0)]
    assert timeline.source_point_to_output(tl, 9) is None


def test_timeline_validation_rejects_unknown_assets():
    tl = timeline.empty(10)
    tl["dialogue"]["items"] = [{"asset_id": "desconocido", "source_start": 1}]
    with pytest.raises(Exception):
        timeline.validate(tl, 10, set())


def test_subtitles_follow_reordered_clips():
    tl = timeline.empty(10)
    tl["video"]["clips"] = [{"id": "a", "in": 5, "out": 8}, {"id": "b", "in": 0, "out": 2}]
    tl["subtitles"]["items"] = [{"id": "s", "start": 6, "end": 7, "text": "Hola", "base": "source"}]
    tl = timeline.validate(tl, 10, set())
    assert timeline.output_subtitles(tl) == [{"start": 1.0, "end": 2.0, "text": "Hola"}]


# ------------------------------------------------------------ texto y subtítulos

def test_pause_parsing_and_estimate():
    parts = tts.parse_script("Hola. [pausa 1,5] ¿Qué tal? [pausa]")
    assert [p["type"] for p in parts] == ["speech", "pause", "speech", "pause"]
    assert parts[1]["seconds"] == 1.5 and parts[3]["seconds"] == tts.DEFAULT_PAUSE
    slow = tts.estimate_duration("Esto es una frase de prueba bastante larga.", speed=0.8)["seconds"]
    fast = tts.estimate_duration("Esto es una frase de prueba bastante larga.", speed=1.5)["seconds"]
    assert slow > fast > 0


def test_pronunciations():
    assert tts.apply_pronunciations("Visita Melilla", {"Melilla": "Meliya"}) == "Visita Meliya"


def test_subtitle_chunks_and_srt():
    blocks = subtitles.chunk_text("Primera frase corta. Segunda frase que es bastante más larga que la anterior y "
                                  "necesita dividirse en líneas.")
    assert all(len(line) <= subtitles.MAX_LINE for b in blocks for line in b.split("\n"))
    timed = subtitles.time_blocks(blocks, 0, 6)
    assert timed[0]["start"] == 0 and abs(timed[-1]["end"] - 6) < 0.01
    srt = subtitles.to_srt(timed)
    assert "00:00:00,000 -->" in srt


# ------------------------------------------------------------ seguimiento y calidad

def test_tracking_associates_moving_face():
    tracks: list[Track] = []
    nid = 1
    for i in range(6):
        nid = associate(tracks, [Detection(i * 0.5, 100 + i * 5, 100, 80, 80, 0.9)], i * 0.5, 1.5, nid)
    assert len(tracks) == 1 and len(tracks[0].detections) == 6


def test_interpolate():
    tr = [{"t": 0, "x": 0, "y": 0, "w": 10, "h": 10}, {"t": 1, "x": 10, "y": 0, "w": 10, "h": 10}]
    assert interpolate(tr, 0.5)["x"] == 5


def _track(h, n=9, step=0.5):
    return [{"t": i * step, "x": 100, "y": 100, "w": h, "h": h, "yaw": 0.0, "brightness": 120.0,
             "profile": False} for i in range(n)]


def test_quality_blocks_small_faces():
    rep = quality.assess_segment(_track(40), [], 0, 4, 0.5)
    assert not rep["ok"] and any("pequeño" in b for b in rep["blocking"])


def test_quality_ok_for_good_material():
    rep = quality.assess_segment(_track(200), [], 0, 4, 0.5)
    assert rep["ok"] and not rep["warnings"]


def test_quality_blocks_when_face_missing():
    rep = quality.assess_segment(_track(200, n=3), [], 0, 4, 0.5)
    assert not rep["ok"]

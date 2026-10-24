"""Detección y seguimiento de rostros con OpenCV.

* **YuNet** (OpenCV Zoo, licencia MIT): detector CNN con 5 puntos faciales.
  Requiere descargar ``face_detection_yunet_2023mar.onnx`` y fijar
  YUNET_MODEL_PATH. Permite estimar el giro de la cabeza.
* **Haar** (incluido en OpenCV, licencia BSD/Apache-2.0): alternativa sin
  descargas, menos precisa y sin puntos faciales; se usa si no hay YuNet.

El seguimiento asocia detecciones muestreadas (cada ``interval`` s) por
solapamiento (IoU) y distancia entre centros. No es reconocimiento facial: no
identifica a nadie, sólo agrupa apariciones de un mismo rostro en el vídeo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from ..config import settings
from ..errors import JobCancelled


@dataclass
class Detection:
    t: float
    x: int
    y: int
    w: int
    h: int
    score: float
    yaw: float | None = None  # -1..1 aprox. (0 = frontal); None si no se puede estimar
    brightness: float | None = None
    profile: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"t": round(self.t, 3), "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "score": round(self.score, 3),
                "yaw": None if self.yaw is None else round(self.yaw, 3),
                "brightness": None if self.brightness is None else round(self.brightness, 1),
                "profile": self.profile}


@dataclass
class Track:
    id: int
    detections: list[Detection] = field(default_factory=list)

    @property
    def last(self) -> Detection:
        return self.detections[-1]


class FaceDetector:
    def __init__(self) -> None:
        mode = settings.face_detector
        self.kind = "haar"
        self._yunet = None
        if mode in ("auto", "yunet") and settings.yunet_model_path and Path(settings.yunet_model_path).is_file():
            self._yunet = cv2.FaceDetectorYN_create(settings.yunet_model_path, "", (320, 320), 0.7, 0.3, 5000)
            self.kind = "yunet"
        elif mode == "yunet":
            raise RuntimeError("FACE_DETECTOR=yunet pero YUNET_MODEL_PATH no apunta a un modelo válido.")
        if self.kind == "haar":
            if not hasattr(cv2, "CascadeClassifier"):
                raise RuntimeError("Esta versión de OpenCV no incluye los clasificadores Haar (OpenCV 5): "
                                   "instale opencv-python-headless<5 o configure YUNET_MODEL_PATH.")
            base = cv2.data.haarcascades
            self._frontal = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
            self._profile = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")

    def detect(self, frame: np.ndarray, t: float = 0.0) -> list[Detection]:
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        out: list[Detection] = []
        if self._yunet is not None:
            self._yunet.setInputSize((w, h))
            _, faces = self._yunet.detect(frame)
            for f in faces if faces is not None else []:
                x, y, fw, fh = (int(v) for v in f[:4])
                re_x, le_x, nose_x = f[4], f[6], f[8]
                eye_mid = (re_x + le_x) / 2
                eye_dist = abs(le_x - re_x) or 1.0
                yaw = float(np.clip((nose_x - eye_mid) / eye_dist, -1, 1))
                out.append(self._finish(gray, t, x, y, fw, fh, float(f[14]), yaw, False))
            return out
        min_size = max(24, int(min(w, h) * 0.05))
        for (x, y, fw, fh) in self._frontal.detectMultiScale(gray, 1.1, 6, minSize=(min_size, min_size)):
            out.append(self._finish(gray, t, x, y, fw, fh, 0.8, 0.0, False))
        if not out:
            for flipped in (False, True):
                g = cv2.flip(gray, 1) if flipped else gray
                for (x, y, fw, fh) in self._profile.detectMultiScale(g, 1.1, 6, minSize=(min_size, min_size)):
                    if flipped:
                        x = w - x - fw
                    out.append(self._finish(gray, t, x, y, fw, fh, 0.6, 0.8 if flipped else -0.8, True))
        return _nms(out)

    @staticmethod
    def _finish(gray, t, x, y, w, h, score, yaw, profile) -> Detection:
        x, y = max(0, int(x)), max(0, int(y))
        crop = gray[y:y + int(h), x:x + int(w)]
        bright = float(crop.mean()) if crop.size else None
        return Detection(t, x, y, int(w), int(h), score, yaw, bright, profile)


def iou(a: Detection | dict, b: Detection | dict) -> float:
    ax, ay, aw, ah = _box(a)
    bx, by, bw, bh = _box(b)
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _box(d) -> tuple[float, float, float, float]:
    if isinstance(d, dict):
        return d["x"], d["y"], d["w"], d["h"]
    return d.x, d.y, d.w, d.h


def _nms(dets: list[Detection], thr: float = 0.3) -> list[Detection]:
    dets = sorted(dets, key=lambda d: d.w * d.h, reverse=True)
    kept: list[Detection] = []
    for d in dets:
        if all(iou(d, k) < thr for k in kept):
            kept.append(d)
    return kept


def associate(tracks: list[Track], dets: list[Detection], t: float, max_gap: float,
              next_id: int) -> int:
    active = [tr for tr in tracks if t - tr.last.t <= max_gap]
    used: set[int] = set()
    pairs = []
    for di, d in enumerate(dets):
        for tr in active:
            l = tr.last
            dist = np.hypot((d.x + d.w / 2) - (l.x + l.w / 2), (d.y + d.h / 2) - (l.y + l.h / 2))
            norm = dist / max(1.0, (l.w + d.w) / 2)
            score = iou(d, l) - 0.25 * norm
            if iou(d, l) > 0.1 or norm < 1.0:
                pairs.append((score, di, tr))
    assigned_tracks: set[int] = set()
    for score, di, tr in sorted(pairs, key=lambda p: p[0], reverse=True):
        if di in used or tr.id in assigned_tracks:
            continue
        tr.detections.append(dets[di])
        used.add(di)
        assigned_tracks.add(tr.id)
    for di, d in enumerate(dets):
        if di not in used:
            tracks.append(Track(next_id, [d]))
            next_id += 1
    return next_id


def analyze_video(path: Path, interval: float = 0.5, max_gap: float = 1.5,
                  on_progress: Callable[[float], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
    detector = FaceDetector()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("No se pudo abrir el vídeo para el análisis facial.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frames / fps if frames else 0
    step = max(1, int(round(interval * fps)))
    tracks: list[Track] = []
    next_id = 1
    idx = 0
    samples = 0
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                if should_cancel and should_cancel():
                    raise JobCancelled()
                ok, frame = cap.retrieve()
                if not ok:
                    break
                t = idx / fps
                next_id = associate(tracks, detector.detect(frame, t), t, max_gap, next_id)
                samples += 1
                if on_progress and frames:
                    on_progress(idx / frames)
            idx += 1
    finally:
        cap.release()
    min_dets = 3 if duration > 3 else 1
    kept = [tr for tr in tracks if len(tr.detections) >= min_dets]
    kept.sort(key=lambda tr: len(tr.detections), reverse=True)
    return {"detector": detector.kind, "interval": interval, "samples": samples,
            "duration": duration, "fps": fps,
            "tracks": [[d.as_dict() for d in tr.detections] for tr in kept]}


def track_stats(track: list[dict], interval: float) -> dict[str, Any]:
    heights = [d["h"] for d in track]
    return {
        "first": track[0]["t"], "last": track[-1]["t"],
        "appearances": len(track),
        "seconds_visible": round(len(track) * interval, 1),
        "median_face_px": int(np.median(heights)) if heights else 0,
    }


def interpolate(track: list[dict], t: float, max_gap: float = 1.0) -> dict | None:
    """Caja interpolada del rostro en el instante ``t`` (None si no hay dato cercano)."""
    if not track:
        return None
    if t <= track[0]["t"]:
        return track[0] if track[0]["t"] - t <= max_gap else None
    if t >= track[-1]["t"]:
        return track[-1] if t - track[-1]["t"] <= max_gap else None
    lo, hi = 0, len(track) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if track[mid]["t"] <= t:
            lo = mid
        else:
            hi = mid
    a, b = track[lo], track[hi]
    if b["t"] - a["t"] > 2 * max_gap:
        return None
    r = (t - a["t"]) / max(1e-6, b["t"] - a["t"])
    return {k: a[k] + (b[k] - a[k]) * r for k in ("x", "y", "w", "h")} | {"t": t}

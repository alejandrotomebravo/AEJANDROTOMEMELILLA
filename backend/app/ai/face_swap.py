"""Sustitución facial.

Proveedor disponible: **InsightFace + inswapper_128**.

LICENCIA (verificada): el código de InsightFace es MIT, pero los modelos
preentrenados (incluido ``inswapper_128.onnx``) sólo se permiten para
investigación no comercial; el uso comercial requiere una licencia de
InsightFace. Por eso el proveedor exige INSIGHTFACE_LICENSE_ACK=true, y los
modelos NO se descargan automáticamente: el administrador debe obtenerlos.

La interfaz ``FaceSwapProvider`` permite sustituir este motor por otro con
licencia comercial sin modificar el resto de la aplicación.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Protocol

import cv2
import numpy as np

from ..config import settings
from ..errors import JobCancelled, ProviderUnavailable, QualityError
from . import face_detect


class FaceSwapProvider(Protocol):
    name: str
    license_note: str

    def prepare_identity(self, photos: list[Path]) -> Any: ...

    def swap_video(self, src: Path, dst: Path, identity: Any, track: list[dict], *, start: float,
                   end: float, fps: float, on_progress: Callable[[float], None] | None,
                   should_cancel: Callable[[], bool] | None) -> dict[str, Any]: ...


def status() -> dict[str, Any]:
    p = settings.faceswap_provider
    if p == "none":
        return {"available": False, "provider": None,
                "reason": "No hay motor de sustitución facial configurado (FACESWAP_PROVIDER)."}
    if p == "insightface":
        try:
            import insightface  # noqa: F401
        except Exception:
            return {"available": False, "provider": p, "reason": "El paquete «insightface» no está instalado."}
        if not settings.insightface_license_ack:
            return {"available": False, "provider": p,
                    "reason": "Los modelos de InsightFace son de uso no comercial. Confirme que su uso "
                              "está permitido o que dispone de licencia (INSIGHTFACE_LICENSE_ACK=true)."}
        if not settings.inswapper_model_path or not Path(settings.inswapper_model_path).is_file():
            return {"available": False, "provider": p, "reason": "Falta el modelo inswapper_128.onnx (INSWAPPER_MODEL_PATH)."}
        return {"available": True, "provider": p, "device": settings.faceswap_device,
                "license": "Modelos InsightFace: no comercial salvo licencia de InsightFace."}
    return {"available": False, "provider": p, "reason": f"Proveedor desconocido: {p}"}


def get_provider() -> FaceSwapProvider:
    st = status()
    if not st["available"]:
        raise ProviderUnavailable(st["reason"])
    return InsightFaceProvider()


def seconds_per_second() -> float:
    return (settings.faceswap_seconds_per_second_gpu if settings.faceswap_device == "cuda"
            else settings.faceswap_seconds_per_second_cpu)


class InsightFaceProvider:
    name = "insightface"
    license_note = "InsightFace inswapper_128: uso no comercial salvo licencia."

    def __init__(self) -> None:
        import insightface
        from insightface.app import FaceAnalysis

        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"] if settings.faceswap_device == "cuda"
                     else ["CPUExecutionProvider"])
        kwargs: dict[str, Any] = {"name": "buffalo_l", "providers": providers}
        if settings.insightface_model_root:
            kwargs["root"] = settings.insightface_model_root
        self.analyzer = FaceAnalysis(**kwargs)
        self.analyzer.prepare(ctx_id=0 if settings.faceswap_device == "cuda" else -1, det_size=(640, 640))
        self.swapper = insightface.model_zoo.get_model(settings.inswapper_model_path, providers=providers)

    def prepare_identity(self, photos: list[Path]) -> Any:
        faces = []
        for p in photos:
            img = cv2.imread(str(p))
            if img is None:
                continue
            found = self.analyzer.get(img)
            if len(found) == 1:
                faces.append(found[0])
        if not faces:
            raise QualityError("No se ha podido extraer un rostro válido de las fotografías de la identidad.")
        # Promedio de embeddings de varias perspectivas para una identidad más estable.
        base = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        emb = np.mean([f.normed_embedding for f in faces], axis=0)
        emb = emb / np.linalg.norm(emb)
        base.embedding = emb * np.linalg.norm(base.embedding)
        base.normed_embedding = emb
        return base

    def swap_video(self, src: Path, dst: Path, identity: Any, track: list[dict], *, start: float,
                   end: float, fps: float, on_progress=None, should_cancel=None) -> dict[str, Any]:
        cap = cv2.VideoCapture(str(src))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        total = max(1, int(round((end - start) * fps)))
        enc = subprocess.Popen(
            [settings.ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{width}x{height}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "16", "-pix_fmt", "yuv420p", str(dst)],
            stdin=subprocess.PIPE)
        ref_emb = None
        swapped = missed = 0
        try:
            for i in range(total):
                if should_cancel and should_cancel():
                    raise JobCancelled()
                ok, frame = cap.read()
                if not ok:
                    break
                t = start + i / fps
                faces = self.analyzer.get(frame)
                target = None
                box = face_detect.interpolate(track, t)
                if box is not None and faces:
                    def _iou(f):
                        x1, y1, x2, y2 = f.bbox
                        return face_detect.iou({"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}, box)
                    best = max(faces, key=_iou)
                    if _iou(best) > 0.2:
                        target = best
                        if ref_emb is None:
                            ref_emb = best.normed_embedding
                if target is None and ref_emb is not None and faces:
                    sims = [float(np.dot(f.normed_embedding, ref_emb)) for f in faces]
                    j = int(np.argmax(sims))
                    if sims[j] > 0.45:
                        target = faces[j]
                if target is not None:
                    frame = self.swapper.get(frame, target, identity, paste_back=True)
                    swapped += 1
                elif box is not None:
                    missed += 1
                assert enc.stdin is not None
                enc.stdin.write(frame.tobytes())
                if on_progress:
                    on_progress((i + 1) / total)
        finally:
            cap.release()
            if enc.stdin:
                enc.stdin.close()
            enc.wait()
        if enc.returncode != 0:
            raise RuntimeError("Error al codificar el fragmento con el rostro sustituido.")
        return {"frames": total, "swapped": swapped, "missed": missed}

"""Person detection + tracking with ultralytics YOLO and ByteTrack.

Only the COCO "person" class (0) is used, conf >= 0.35, imgsz 1280 (the streams'
width, so people across the room still count), CPU. Every person detected in a
frame counts. ByteTrack only adds ids: it hides a person until it has matched them
in two frames in a row, and frames taken seconds apart rarely match, so its own
output would undercount. Green boxes (BGR 0,200,0) carry the id once there is
one; a count overlay sits top-left. No
crops, no faces, no identification: a box and a number per person is all that
is ever produced, and nothing here is written to the database.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import config

GREEN = (0, 200, 0)  # BGR
PERSON_CLASS = 0
TRACK_CONF = 0.1  # ByteTrack also takes low-score boxes, to keep a track through a weak frame


@dataclass(frozen=True)
class Detection:
    track_id: int | None
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float


class PersonTracker:
    """YOLO + ByteTrack with persistent ids. One instance per camera window."""

    def __init__(
        self,
        model_name: str = config.MODEL_NAME,
        conf: float = config.CONF,
        imgsz: int = config.IMGSZ,
        device: str = "cpu",
    ):
        from ultralytics import YOLO

        config.ensure_dirs()
        path = config.MODEL_DIR / model_name
        # YOLO() downloads the weights to this path on first use.
        self.model = YOLO(str(path if path.exists() else path))
        self.model_name = Path(model_name).stem
        self.conf = conf
        self.imgsz = imgsz
        self.device = device
        self._tracker = None

    def reset(self) -> None:
        """Forget tracker state between windows (ids restart)."""
        self._tracker = None

    def _new_tracker(self):
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.utils import YAML, IterableSimpleNamespace
        from ultralytics.utils.checks import check_yaml

        return BYTETracker(IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml"))))

    def track(self, frame_bgr: np.ndarray) -> list[Detection]:
        """Every person in the frame (conf >= self.conf), with a track id once ByteTrack has one."""
        results = self.model.predict(
            frame_bgr,
            classes=[PERSON_CLASS],
            conf=min(TRACK_CONF, self.conf),
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )
        if not results or results[0].boxes is None:
            return []
        boxes = results[0].boxes.cpu().numpy()
        if self._tracker is None:
            self._tracker = self._new_tracker()
        tracks = self._tracker.update(boxes, frame_bgr)
        # Each row is x1, y1, x2, y2, id, score, class, index of the detection it came from.
        ids = {int(row[-1]): int(row[4]) for row in tracks}
        return [
            Detection(ids.get(i), int(x1), int(y1), int(x2), int(y2), float(c))
            for i, ((x1, y1, x2, y2), c) in enumerate(zip(boxes.xyxy, boxes.conf, strict=True))
            if c >= self.conf
        ]

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        """Detection only (no ids); used when a single frame is all there is."""
        results = self.model.predict(
            frame_bgr, classes=[PERSON_CLASS], conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False
        )
        return boxes_to_detections(results[0].boxes if results else None)


def boxes_to_detections(boxes) -> list[Detection]:
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else [None] * len(xyxy)
    out = []
    for (x1, y1, x2, y2), c, tid in zip(xyxy, confs, ids, strict=True):
        out.append(Detection(None if tid is None else int(tid), int(x1), int(y1), int(x2), int(y2), float(c)))
    return out


def annotate(frame_bgr: np.ndarray, dets: Sequence[Detection], title: str = "", extra: str = "") -> np.ndarray:
    """Return a copy with green boxes, ids and a count overlay."""
    img = frame_bgr.copy()
    h, w = img.shape[:2]
    scale = max(0.5, min(1.2, w / 1280))
    thick = max(1, int(round(2 * scale)))
    for d in dets:
        cv2.rectangle(img, (d.x1, d.y1), (d.x2, d.y2), GREEN, thick)
        label = f"#{d.track_id}" if d.track_id is not None else f"{d.conf:.2f}"
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, 1)
        y = max(d.y1, th + base + 2)
        cv2.rectangle(img, (d.x1, y - th - base - 2), (d.x1 + tw + 4, y), GREEN, -1)
        cv2.putText(img, label, (d.x1 + 2, y - base), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (0, 0, 0), 1, cv2.LINE_AA)
    text = f"{len(dets)} people"
    if title:
        text = f"{title}  |  {text}"
    if extra:
        text += f"  |  {extra}"
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale, 2)
    cv2.rectangle(img, (0, 0), (tw + 16, th + base + 14), (0, 0, 0), -1)
    cv2.putText(img, text, (8, th + 7), cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale, GREEN, 2, cv2.LINE_AA)
    return img


def run_window(
    tracker: PersonTracker, frames: Iterable[tuple[float, np.ndarray]]
) -> tuple[list[int], set[int], list[tuple[float, np.ndarray, list[Detection]]]]:
    """Track every frame of a window. Returns (per-frame counts, distinct ids, per-frame results)."""
    counts: list[int] = []
    ids: set[int] = set()
    per_frame = []
    for ts, frame in frames:
        dets = tracker.track(frame)
        counts.append(len(dets))
        ids.update(d.track_id for d in dets if d.track_id is not None)
        per_frame.append((ts, frame, dets))
    return counts, ids, per_frame


def write_annotated_mp4(
    per_frame: Sequence[tuple[float, np.ndarray, Sequence[Detection]]],
    path: str | os.PathLike,
    title: str = "",
    fps: float = 2.0,
    hold: int = 1,
) -> Path:
    """Write the annotated frames of a window to an MP4 (H.264 via ffmpeg when available).

    `hold` repeats each frame so a 2 s sampling interval plays at a watchable
    pace; with fps=2 and hold=1 the clip lasts as long as the window did.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not per_frame:
        raise ValueError("no frames")
    h, w = per_frame[0][1].shape[:2]
    tmp = path.with_suffix(".raw.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n = len(per_frame)
    for i, (_ts, frame, dets) in enumerate(per_frame):
        img = annotate(frame, dets, title, f"frame {i + 1}/{n}")
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h))
        for _ in range(hold):
            writer.write(img)
    writer.release()
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if os.path.exists(ffmpeg):
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(tmp),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(path),
            ],
            check=True,
        )
        tmp.unlink(missing_ok=True)
    else:
        tmp.rename(path)
    return path


def status_tile(text: str, size: tuple[int, int] = (640, 360), title: str = "") -> np.ndarray:
    """A dark tile with a status message, for halls with no frames."""
    w, h = size
    img = np.full((h, w, 3), 24, dtype=np.uint8)
    if title:
        cv2.putText(img, title, (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, GREEN, 2, cv2.LINE_AA)
    y = 90
    for line in text.split("\n"):
        cv2.putText(img, line[:70], (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1, cv2.LINE_AA)
        y += 26
    return img


def contact_sheet(tiles: Sequence[np.ndarray], cols: int = 2, tile_size: tuple[int, int] = (640, 360)) -> np.ndarray:
    w, h = tile_size
    rows = (len(tiles) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * w, 3), dtype=np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet[r * h : (r + 1) * h, c * w : (c + 1) * w] = cv2.resize(t, (w, h))
    return sheet

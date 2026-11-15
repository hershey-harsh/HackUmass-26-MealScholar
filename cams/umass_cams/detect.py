"""Person counting + tracking with ultralytics YOLO and ByteTrack.

Counting path (benchmarked 2026-11-14 on 14 frames of the three primary cams, counted
by eye three times each; see config.py for the numbers): yolo11l on the full 1280x720
frame at imgsz 1280 plus the same model on 2x2 tiles with 20 % overlap, each tile
upsampled to imgsz 1280 so the small, far-away, half-hidden people the full-frame pass
misses get enough pixels. The five sets of boxes are merged with one greedy NMS (IoU
0.5), so a person seen by two tiles or by a tile and the full frame is one box; a box
a tile edge cut is a fragment and is dropped when a kept box already holds most of it
(config.SEAM_CONTAIN; see merge_boxes), so a person seated on a seam is one box, not
three. The merged set is cut at a per-camera confidence (config.conf_for): a close-up
cam over-counts at the conf a hazy, far-away one needs. Only the COCO "person" class (0) is used. Every box
at or above the camera's conf is one person; nothing is extrapolated.

ByteTrack only adds ids (the clip labels them): its thresholds follow the camera's
conf so every counted box can carry an id, and the detection score is not fused into
the match cost (seated people with weak scores would never match). A tracker is reset
per camera window, so ids restart.

Counting costs ~2.3 s a frame on the Mac mini's CPU (launchd gives it 4 threads) and
~0.6 s on its GPU (MPS; counts identical to CPU on the 14 frames), so a window is
counted on a few frames spread evenly over it (config.COUNT_FRAMES) while the clip
keeps every frame: between two counted frames the clip shows the boxes of the last
counted frame (see run_window). Green boxes (BGR 0,200,0) carry the id once there is one; a count overlay
sits top-left. No crops, no faces, no identification: a box and a number per person is
all that is ever produced, and nothing here is written to the database. write_clip
keeps only each hall's latest boxed clip and poster, as files for the app.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import config

log = logging.getLogger("umass_cams.detect")

GREEN = (0, 200, 0)  # BGR
PERSON_CLASS = 0
MAX_DET = 1000  # per image; a packed Worcester view has ~80 people and many more weak boxes below the cut


@dataclass(frozen=True)
class Detection:
    track_id: int | None
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float


# --------------------------------------------------------------------------
# Tiling and merging (pure numpy, tested without a model)
# --------------------------------------------------------------------------


def tile_grid(w: int, h: int, cols: int, rows: int, overlap: float) -> list[tuple[int, int, int, int]]:
    """(x1, y1, x2, y2) of cols x rows tiles that cover a w x h frame, neighbours sharing `overlap` of a
    tile's width/height. 1280x720, 2x2, 0.2: x [0,711],[569,1280]; y [0,400],[320,720] (tiles 711x400)."""
    if cols < 1 or rows < 1 or not 0 <= overlap < 1:
        raise ValueError("need cols, rows >= 1 and 0 <= overlap < 1")
    tw = w / (cols - (cols - 1) * overlap)
    th = h / (rows - (rows - 1) * overlap)
    sx, sy = tw * (1 - overlap), th * (1 - overlap)
    tiles = []
    for r in range(rows):
        for c in range(cols):
            x1, y1 = int(round(c * sx)), int(round(r * sy))
            x2 = w if c == cols - 1 else int(round(x1 + tw))
            y2 = h if r == rows - 1 else int(round(y1 + th))
            tiles.append((x1, y1, x2, y2))
    return tiles


def merge_boxes(
    boxes: np.ndarray, iou_thr: float, cut: np.ndarray | None = None, contain: float | None = None
) -> np.ndarray:
    """Greedy NMS over (N, 5) rows of x1, y1, x2, y2, conf from any number of passes: in descending conf,
    a box is dropped when a kept box overlaps it by IoU > iou_thr. Returns the kept rows, conf descending.
    Without `cut`, which boxes survive above any conf c depends only on the boxes above c, so the merged
    set can be cut at a higher conf afterwards and is the same as if the lower boxes had never been there.

    `cut` flags boxes a tile edge cut (seam_cut): a fragment of a person, not the person. Whole boxes are
    merged first; a cut box is then also dropped when a kept box holds more than `contain` of its area,
    and that kept box takes the fragment's conf if it is higher (the fragment is evidence for the same
    person). A cut box nothing holds is kept: that person was only seen cut. Without this, a seated
    person on a seam was counted two or three times (frame, tile above, tile below)."""
    boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 5)
    if len(boxes) == 0:
        return boxes
    cut = np.zeros(len(boxes), dtype=bool) if cut is None else np.asarray(cut, dtype=bool).reshape(-1)
    order = np.lexsort((-boxes[:, 4], cut))  # whole boxes first, then cut ones; conf descending within
    b, c = boxes[order].copy(), cut[order]
    areas = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    keep: list[int] = []
    dropped = np.zeros(len(b), dtype=bool)
    for i in range(len(b)):
        if dropped[i]:
            continue
        keep.append(i)
        rest = slice(i + 1, None)
        xx1 = np.maximum(b[i, 0], b[rest, 0])
        yy1 = np.maximum(b[i, 1], b[rest, 1])
        xx2 = np.minimum(b[i, 2], b[rest, 2])
        yy2 = np.minimum(b[i, 3], b[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / np.maximum(areas[i] + areas[rest] - inter, 1e-6)
        sup = iou > iou_thr
        if contain is not None:
            held = c[rest] & ~dropped[rest] & (inter / np.maximum(areas[rest], 1e-6) > contain)
            if held.any():
                b[i, 4] = max(b[i, 4], b[rest][held][:, 4].max())
                sup |= held
        dropped[rest] |= sup
    out = b[keep]
    return out[np.argsort(-out[:, 4], kind="stable")]


def seam_cut(boxes: np.ndarray, tile: tuple[int, int, int, int], w: int, h: int, tol: float = 2.0) -> np.ndarray:
    """Which (N, 5) boxes in tile coordinates touch an edge of `tile` that is not a frame border, i.e. were
    cut by the tiling (a person who continues in the neighbouring tile or the full frame)."""
    x1, y1, x2, y2 = tile
    c = np.zeros(len(boxes), dtype=bool)
    if len(boxes) == 0:
        return c
    if x1 > 0:
        c |= boxes[:, 0] <= tol
    if x2 < w:
        c |= boxes[:, 2] >= (x2 - x1) - tol
    if y1 > 0:
        c |= boxes[:, 1] <= tol
    if y2 < h:
        c |= boxes[:, 3] >= (y2 - y1) - tol
    return c


def result_boxes(result) -> np.ndarray:
    """(N, 5) x1, y1, x2, y2, conf of one ultralytics result (empty when it has no boxes)."""
    b = getattr(result, "boxes", None)
    if b is None or len(b) == 0:
        return np.zeros((0, 5), dtype=np.float32)
    xyxy = b.xyxy.cpu().numpy()
    conf = b.conf.cpu().numpy()
    return np.concatenate([xyxy, conf[:, None]], axis=1).astype(np.float32)


class _Dets:
    """The (N, 5) merged boxes in the shape ByteTrack reads: conf, cls, xywh, and boolean indexing."""

    def __init__(self, boxes: np.ndarray):
        self.boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 5)
        self.xyxy = self.boxes[:, :4]
        self.conf = self.boxes[:, 4]
        self.cls = np.zeros(len(self.boxes), dtype=np.float32)
        wh = self.xyxy[:, 2:4] - self.xyxy[:, 0:2]
        self.xywh = np.concatenate([self.xyxy[:, 0:2] + wh / 2, wh], axis=1)

    def __len__(self) -> int:
        return len(self.boxes)

    def __getitem__(self, idx) -> _Dets:
        return _Dets(self.boxes[idx])


def resolve_device(device: str = config.DEVICE) -> str:
    """ "auto" -> "mps" on an Apple GPU, else "cpu"; anything else is passed through."""
    if device != "auto":
        return device
    try:
        import torch

        return "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:
        return "cpu"


# --------------------------------------------------------------------------
# Detector + tracker
# --------------------------------------------------------------------------


class PersonTracker:
    """YOLO (full frame + tiles, merged) + ByteTrack with persistent ids. One instance per camera window.

    `conf` is the camera's cut (config.conf_for). `model` takes a loaded ultralytics model (tests pass a
    fake with the same predict() shape); otherwise the weights `model_name` are loaded from config.MODEL_DIR.
    """

    def __init__(
        self,
        model_name: str = config.MODEL_NAME,
        conf: float = config.CONF_DEFAULT,
        imgsz: int = config.IMGSZ,
        device: str = config.DEVICE,
        tiles: tuple[int, int] = config.TILES,
        tile_imgsz: int = config.TILE_IMGSZ,
        overlap: float = config.TILE_OVERLAP,
        merge_iou: float = config.MERGE_IOU,
        seam_contain: float | None = config.SEAM_CONTAIN,
        model=None,
    ):
        if model is None:
            from ultralytics import YOLO

            config.ensure_dirs()
            # YOLO() downloads the weights to this path on first use; the Mac mini has them in MODEL_DIR.
            model = YOLO(str(config.MODEL_DIR / model_name))
        self.model = model
        self.model_name = Path(model_name).stem
        if not 0 < conf <= 1:
            raise ValueError("conf must be in (0, 1]")
        self.conf = float(conf)
        # Boxes below the cut still feed ByteTrack's second (low-score) association, which keeps a track
        # alive through one weak frame; nothing below the cut is ever counted or drawn.
        self.track_low = self.conf / 2
        self.imgsz = imgsz
        self.device = resolve_device(device)
        self.tiles = tiles
        self.tile_imgsz = tile_imgsz
        self.overlap = overlap
        self.merge_iou = merge_iou
        self.seam_contain = seam_contain
        self._tracker = None
        self.last_s = 0.0  # wall seconds of the last boxes() call

    @property
    def setup(self) -> str:
        """Short description for logs and the occupancy document."""
        cols, rows = self.tiles
        tiles = f"+{cols}x{rows}@{self.tile_imgsz}" if cols * rows > 1 else ""
        seam = f" seam{self.seam_contain:g}" if tiles and self.seam_contain is not None else ""
        return f"{self.model_name} full@{self.imgsz}{tiles} iou{self.merge_iou:g}{seam} conf{self.conf:g} {self.device}"

    def reset(self) -> None:
        """Forget tracker state between windows (ids restart)."""
        self._tracker = None

    def _new_tracker(self):
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.utils import YAML, IterableSimpleNamespace
        from ultralytics.utils.checks import check_yaml

        args = YAML.load(check_yaml("bytetrack.yaml"))
        args.update(
            track_high_thresh=self.conf,  # first association + new tracks: every counted box can get an id
            new_track_thresh=self.conf,
            track_low_thresh=self.track_low,
            fuse_score=False,  # IoU only: a 0.12 box of a seated person would never match with the score fused in
        )
        return BYTETracker(IterableSimpleNamespace(**args))

    def _predict(self, images, imgsz: int) -> list[np.ndarray]:
        """Person boxes (frame or tile coordinates) of one or several same-sized images, conf >= track_low."""
        kw = {
            "classes": [PERSON_CLASS],
            "conf": self.track_low,
            "imgsz": imgsz,
            "device": self.device,
            "verbose": False,
            "max_det": MAX_DET,
        }
        try:
            results = self.model.predict(images, **kw)
        except Exception as e:
            if self.device == "cpu":
                raise
            # The GPU path failed (Metal unavailable to this process, out of memory): finish on the CPU.
            log.warning("%s inference failed (%s: %s); switching to cpu", self.device, type(e).__name__, e)
            self.device = "cpu"
            kw["device"] = "cpu"
            results = self.model.predict(images, **kw)
        return [result_boxes(r) for r in results]

    def boxes(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Merged (N, 5) person boxes of one frame with conf >= track_low, conf descending: the full-frame
        pass at imgsz, the tiles at tile_imgsz (each upsampled to it), one greedy NMS over the union with
        the seam fragments folded in (merge_boxes)."""
        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]
        parts = self._predict(frame_bgr, self.imgsz)
        cuts = [np.zeros(len(parts[0]), dtype=bool)]
        cols, rows = self.tiles
        if cols * rows > 1:
            grid = tile_grid(w, h, cols, rows, self.overlap)
            crops = [np.ascontiguousarray(frame_bgr[y1:y2, x1:x2]) for x1, y1, x2, y2 in grid]
            for tile, b in zip(grid, self._predict(crops, self.tile_imgsz), strict=True):
                if len(b):
                    cuts.append(seam_cut(b, tile, w, h))
                    b = b.copy()
                    b[:, [0, 2]] += tile[0]
                    b[:, [1, 3]] += tile[1]
                    parts.append(b)
        merged = merge_boxes(np.concatenate(parts), self.merge_iou, np.concatenate(cuts), self.seam_contain)
        self.last_s = time.perf_counter() - t0
        return merged

    def track(self, frame_bgr: np.ndarray) -> list[Detection]:
        """Every person in the frame (conf >= self.conf), with a track id once ByteTrack has one."""
        boxes = self.boxes(frame_bgr)
        if self._tracker is None:
            self._tracker = self._new_tracker()
        tracks = self._tracker.update(_Dets(boxes), frame_bgr)
        # Each row is x1, y1, x2, y2, id, score, class, index of the detection it came from.
        ids = {int(row[-1]): int(row[4]) for row in tracks}
        return [
            Detection(ids.get(i), int(x1), int(y1), int(x2), int(y2), float(c))
            for i, (x1, y1, x2, y2, c) in enumerate(boxes)
            if c >= self.conf
        ]

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        """Detection only (no ids); used when a single frame is all there is."""
        return [
            Detection(None, int(x1), int(y1), int(x2), int(y2), float(c))
            for x1, y1, x2, y2, c in self.boxes(frame_bgr)
            if c >= self.conf
        ]


def boxes_to_detections(boxes) -> list[Detection]:
    """ultralytics Boxes (optionally with ids) -> Detections."""
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else [None] * len(xyxy)
    out = []
    for (x1, y1, x2, y2), c, tid in zip(xyxy, confs, ids, strict=True):
        out.append(Detection(None if tid is None else int(tid), int(x1), int(y1), int(x2), int(y2), float(c)))
    return out


# --------------------------------------------------------------------------
# Windows: which frames are counted
# --------------------------------------------------------------------------


def count_plan(n_frames: int, count_frames: int) -> list[int]:
    """Indices of the frames to count: `count_frames` of `n_frames`, spread evenly, first and last included
    (32 frames, 6 counted: 0, 6, 12, 19, 25, 31). Fewer frames than that: all of them."""
    if n_frames <= 0:
        return []
    k = max(1, min(count_frames, n_frames))
    if k == 1:
        return [0]
    return sorted({int(round(i * (n_frames - 1) / (k - 1))) for i in range(k)})


@dataclass
class WindowResult:
    counts: list[int] = field(default_factory=list)  # one per counted frame, in time order
    ids: set[int] = field(default_factory=set)  # distinct track ids over the counted frames
    per_frame: list[tuple[float, np.ndarray, list[Detection]]] = field(default_factory=list)  # every frame
    counted: list[int] = field(default_factory=list)  # indices into per_frame that were counted
    seconds: float = 0.0  # wall time spent in the detector

    @property
    def planned_short(self) -> bool:
        return len(self.counted) < len(self.per_frame)


def run_window(
    tracker: PersonTracker,
    frames: Iterable[tuple[float, np.ndarray]],
    count_frames: int = config.COUNT_FRAMES,
    deadline: float | None = None,
    clock: Callable[[], float] = time.time,
) -> WindowResult:
    """Count the planned frames of a window (count_plan), tracking ids across them. Every frame comes back in
    per_frame for the clip; a frame that was not counted carries the boxes of the last counted one, so the clip
    plays at the capture rate with boxes that are at most one plan step old (2.5 s at 6 of 32 frames).

    `deadline` (clock() units) is a budget: the first planned frame is always counted; a later one is skipped
    when it would end past the deadline (judged by the slowest frame so far), and the count uses what was
    counted. The frames not counted then hold the last boxes as well."""
    frames = list(frames)
    res = WindowResult()
    planned = set(count_plan(len(frames), count_frames))
    dets: list[Detection] = []
    worst = 0.0
    for i, (ts, frame) in enumerate(frames):
        if i in planned and (not res.counted or deadline is None or clock() + worst <= deadline):
            t0 = clock()
            dets = tracker.track(frame)
            dt = clock() - t0
            worst = max(worst, dt)
            res.seconds += dt
            res.counts.append(len(dets))
            res.ids.update(d.track_id for d in dets if d.track_id is not None)
            res.counted.append(i)
        res.per_frame.append((ts, frame, dets))
    return res


# --------------------------------------------------------------------------
# Drawing and clips
# --------------------------------------------------------------------------


def annotate(
    frame_bgr: np.ndarray,
    dets: Sequence[Detection],
    title: str = "",
    extra: str = "",
    *,
    count: bool = True,
    label_conf: bool = True,
) -> np.ndarray:
    """Return a copy with green boxes, ids and a title overlay; with `count`, that frame's count too. The app's
    clips leave it out: the app shows the window's median, and a per-frame count would disagree with it.
    A box without an id is labelled with its conf unless `label_conf` is off (the clips: 70 small labels
    would hide the people)."""
    img = frame_bgr.copy()
    h, w = img.shape[:2]
    scale = max(0.5, min(1.2, w / 1280))
    thick = max(1, int(round(2 * scale)))
    for d in dets:
        cv2.rectangle(img, (d.x1, d.y1), (d.x2, d.y2), GREEN, thick)
        if d.track_id is None and not label_conf:
            continue
        label = f"#{d.track_id}" if d.track_id is not None else f"{d.conf:.2f}"
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, 1)
        y = max(d.y1, th + base + 2)
        cv2.rectangle(img, (d.x1, y - th - base - 2), (d.x1 + tw + 4, y), GREEN, -1)
        cv2.putText(img, label, (d.x1 + 2, y - base), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (0, 0, 0), 1, cv2.LINE_AA)
    text = f"{len(dets)} people" if count else ""
    if title:
        text = f"{title}  |  {text}" if text else title
    if extra:
        text += f"  |  {extra}"
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale, 2)
    cv2.rectangle(img, (0, 0), (tw + 16, th + base + 14), (0, 0, 0), -1)
    cv2.putText(img, text, (8, th + 7), cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale, GREEN, 2, cv2.LINE_AA)
    return img


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


def clip_fps(per_frame: Sequence[tuple[float, np.ndarray, Sequence[Detection]]], default: float = 2.0) -> float:
    """Frames per second of the capture itself, so the clip plays back in real time."""
    if len(per_frame) < 2:
        return default
    span = per_frame[-1][0] - per_frame[0][0]
    if span <= 0:
        return default
    return max(0.5, min(30.0, (len(per_frame) - 1) / span))


def write_clip(
    per_frame: Sequence[tuple[float, np.ndarray, Sequence[Detection]]],
    mp4_path: str | os.PathLike,
    poster_path: str | os.PathLike,
    title: str = "",
    fps: float | None = None,
    size: tuple[int, int] = config.CLIP_SIZE,
    ffmpeg: str | None = None,
    timeout: float = 20.0,
) -> tuple[Path, Path]:
    """The window's boxed frames as an H.264 MP4 (browsers play it inline) plus a JPEG poster of the
    last frame, both at `size`. Each file is written next to its target and swapped in with
    os.replace, so a reader never sees half a clip. Raises when ffmpeg is missing, fails or takes over
    `timeout` seconds after the last frame (it is killed); the previous clip then stays as it was."""
    mp4_path, poster_path = Path(mp4_path), Path(poster_path)
    if not per_frame:
        raise ValueError("no frames")
    ffmpeg = ffmpeg or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError("ffmpeg not found")
    w, h = size
    fps = fps or clip_fps(per_frame)
    mp4_path.parent.mkdir(parents=True, exist_ok=True)
    poster_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_mp4 = mp4_path.with_name(f".{mp4_path.name}.{os.getpid()}.tmp")
    tmp_jpg = poster_path.with_name(f".{poster_path.name}.{os.getpid()}.tmp")
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{w}x{h}",
        "-r",
        f"{fps:.4f}",
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "26",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(tmp_mp4),
    ]
    for stale in (
        *mp4_path.parent.glob(f".{mp4_path.name}.*.tmp"),
        *poster_path.parent.glob(f".{poster_path.name}.*.tmp"),
    ):
        stale.unlink(missing_ok=True)  # left by a pass that was killed mid-encode
    last = None
    try:
        with tempfile.TemporaryFile() as errf:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf)
            try:
                for _ts, frame, dets in per_frame:
                    img = annotate(frame, dets, title, count=False, label_conf=False)
                    last = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
                    proc.stdin.write(np.ascontiguousarray(last).tobytes())
            except BrokenPipeError:
                pass  # ffmpeg quit early; its exit code says why
            finally:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
                try:
                    code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise
            errf.seek(0)
            err = errf.read().decode(errors="replace")[-200:]
        if code != 0:
            raise RuntimeError(f"ffmpeg exited {code}: {err.strip()}")
        ok, jpg = cv2.imencode(".jpg", last, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            raise RuntimeError("poster encode failed")
        tmp_jpg.write_bytes(jpg.tobytes())
        os.replace(tmp_mp4, mp4_path)
        os.replace(tmp_jpg, poster_path)
    finally:
        tmp_mp4.unlink(missing_ok=True)
        tmp_jpg.unlink(missing_ok=True)
    return mp4_path, poster_path


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

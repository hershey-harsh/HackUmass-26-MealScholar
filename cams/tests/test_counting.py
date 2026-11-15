"""The counting path: tiles + full frame merged into one box per person, per-camera conf, ids, frame plan."""

from dataclasses import dataclass

import numpy as np
import pytest
from umass_cams import config, detect, sample
from umass_cams.detect import Detection, PersonTracker, count_plan, merge_boxes, run_window, tile_grid


def test_tile_grid_2x2_matches_the_benchmark():
    expected = [(0, 0, 711, 400), (569, 0, 1280, 400), (0, 320, 711, 720), (569, 320, 1280, 720)]
    assert tile_grid(1280, 720, 2, 2, 0.2) == expected
    assert tile_grid(1280, 720, 1, 1, 0.2) == [(0, 0, 1280, 720)]


@pytest.mark.parametrize("cols,rows", [(2, 2), (3, 2), (1, 3)])
def test_tile_grid_covers_the_frame_and_neighbours_overlap(cols, rows):
    w, h, overlap = 1280, 720, 0.2
    grid = tile_grid(w, h, cols, rows, overlap)
    assert len(grid) == cols * rows
    covered = np.zeros((h, w), dtype=np.uint8)
    for x1, y1, x2, y2 in grid:
        assert 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h
        covered[y1:y2, x1:x2] += 1
    assert covered.min() >= 1  # every pixel is in some tile
    tw, th = grid[0][2] - grid[0][0], grid[0][3] - grid[0][1]
    for r in range(rows):
        for c in range(cols - 1):
            a, b = grid[r * cols + c], grid[r * cols + c + 1]
            assert a[2] - b[0] >= overlap * tw - 1  # horizontal neighbours share a strip
    for r in range(rows - 1):
        a, b = grid[r * cols], grid[(r + 1) * cols]
        assert a[3] - b[1] >= overlap * th - 1
    with pytest.raises(ValueError):
        tile_grid(w, h, 0, 1, 0.2)


def test_merge_boxes_keeps_one_box_per_person():
    person = [100, 100, 160, 260]
    boxes = np.array(
        [
            [*person, 0.40],  # the full-frame pass
            [102, 98, 158, 262, 0.55],  # the same person from a tile, a little off
            [100, 100, 160, 200, 0.30],  # the same person cut by a tile edge (IoU 0.62 with the whole box)
            [300, 100, 360, 260, 0.70],  # someone else
            [330, 100, 390, 260, 0.20],  # a neighbour overlapping that person by a third: kept
        ],
        dtype=np.float32,
    )
    kept = merge_boxes(boxes, 0.5)
    assert kept[:, 4].tolist() == pytest.approx([0.70, 0.55, 0.20])
    assert kept[1].tolist() == pytest.approx([102, 98, 158, 262, 0.55])  # the best box of the pair survives
    assert merge_boxes(np.zeros((0, 5)), 0.5).shape == (0, 5)
    assert len(merge_boxes(boxes[:1], 0.5)) == 1


def test_merge_boxes_folds_seam_fragments_into_the_box_that_holds_them():
    whole = [300, 100, 360, 300, 0.30]  # a seated person seen whole by the full frame
    legs = [302, 200, 358, 300, 0.55]  # the tile below cut them at y=200: IoU with the whole box is 0.5, not more
    head = [300, 100, 360, 160, 0.40]  # the tile above cut them at y=160
    alone = [600, 150, 650, 200, 0.20]  # cut by a tile edge, nothing holds it: a person only seen cut
    behind = [320, 110, 340, 150, 0.25]  # a small whole box inside the big one (someone seated behind): kept
    boxes = np.array([whole, legs, head, alone, behind], dtype=np.float32)
    cut = np.array([False, True, True, True, False])
    kept = merge_boxes(boxes, 0.5, cut, 0.8)
    assert len(kept) == 3
    assert kept[0].tolist() == pytest.approx([300, 100, 360, 300, 0.55])  # the whole box, with the fragment's conf
    assert kept[1].tolist() == pytest.approx(behind) and kept[2].tolist() == pytest.approx(alone)
    # with no cut flags (or no contain rule) the fragments survive as separate boxes
    assert len(merge_boxes(boxes, 0.5)) == 5 and len(merge_boxes(boxes, 0.5, cut, None)) == 5
    # a cut box is judged against whole boxes even when its conf is higher: one person, one box
    flipped = boxes.copy()
    flipped[0, 4], flipped[1, 4] = 0.10, 0.90
    assert len(merge_boxes(flipped, 0.5, cut, 0.8)) == 3 and merge_boxes(flipped, 0.5, cut, 0.8)[0, 4] == 0.90


def test_seam_cut_flags_boxes_on_inner_tile_edges_only():
    grid = tile_grid(1280, 720, 2, 2, 0.2)
    boxes = np.array(
        [[0, 10, 50, 100, 0.5], [600, 10, 711, 100, 0.5], [10, 300, 60, 400, 0.5], [100, 100, 200, 200, 0.5]],
        dtype=np.float32,
    )
    # top-left tile (0, 0, 711, 400): its left and top are frame borders, its right and bottom are seams
    assert detect.seam_cut(boxes, grid[0], 1280, 720).tolist() == [False, True, True, False]
    # bottom-right tile (569, 320, 1280, 720): its left and top are seams
    assert detect.seam_cut(boxes, grid[3], 1280, 720).tolist() == [True, False, False, False]
    assert detect.seam_cut(np.zeros((0, 5)), grid[0], 1280, 720).tolist() == []


def test_merge_boxes_cut_afterwards_equals_cut_before():
    """What survives above a conf never depends on the boxes below it, so one merged set serves every cut."""
    rng = np.random.default_rng(3)
    xy = rng.uniform(0, 1200, (120, 2))
    wh = rng.uniform(20, 80, (120, 2))
    boxes = np.concatenate([xy, xy + wh, rng.uniform(0.02, 1.0, (120, 1))], axis=1).astype(np.float32)
    boxes = np.concatenate([boxes, boxes + [3, 3, 3, 3, -0.01]])  # every box has a near duplicate
    merged = merge_boxes(boxes, 0.5)
    for cut in (0.05, 0.10, 0.35):
        after = merged[merged[:, 4] >= cut]
        before = merge_boxes(boxes[boxes[:, 4] >= cut], 0.5)
        assert after.tolist() == before.tolist()


# --------------------------------------------------------------------------
# A fake ultralytics model: sees `people` in whatever image (full frame or tile) it is given
# --------------------------------------------------------------------------


@dataclass
class Person:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    tiles_only: bool = False  # too small for the full-frame pass
    full_only: bool = False  # only whole in the full frame


class _Arr:
    def __init__(self, a):
        self.a = np.asarray(a, dtype=np.float32)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Boxes:
    def __init__(self, rows):
        rows = np.asarray(rows, dtype=np.float32).reshape(-1, 5)
        self.xyxy, self.conf = _Arr(rows[:, :4]), _Arr(rows[:, 4])

    def __len__(self):
        return len(self.conf.a)


class _Result:
    def __init__(self, rows):
        self.boxes = _Boxes(rows)


class FakeModel:
    """Finds the region of the frame an image is (the frame itself or one tile), and reports the people
    whose box lies at least half inside it, clipped to it, in the image's own coordinates."""

    def __init__(self, frame: np.ndarray, people: list[Person], grid):
        self.frame, self.people, self.grid = frame, people, grid
        self.calls: list[tuple[int, int]] = []  # (number of images, imgsz) per predict call

    def _region(self, img):
        if img.shape == self.frame.shape and np.array_equal(img, self.frame):
            return 0, 0, self.frame.shape[1], self.frame.shape[0]
        for x1, y1, x2, y2 in self.grid:
            if img.shape == self.frame[y1:y2, x1:x2].shape and np.array_equal(img, self.frame[y1:y2, x1:x2]):
                return x1, y1, x2, y2
        raise AssertionError("predict() got an image that is neither the frame nor one of its tiles")

    def predict(self, images, **kw):
        single = isinstance(images, np.ndarray)
        images = [images] if single else images
        self.calls.append((len(images), kw["imgsz"]))
        assert kw["classes"] == [detect.PERSON_CLASS] and kw["device"] == "cpu"
        out = []
        for img in images:
            x1, y1, x2, y2 = self._region(img)
            full = (x2 - x1, y2 - y1) == (self.frame.shape[1], self.frame.shape[0])
            rows = []
            for p in self.people:
                if p.conf < kw["conf"] or (full and p.tiles_only) or (not full and p.full_only):
                    continue
                ix1, iy1, ix2, iy2 = max(p.x1, x1), max(p.y1, y1), min(p.x2, x2), min(p.y2, y2)
                inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                if inter < 0.5 * (p.x2 - p.x1) * (p.y2 - p.y1):
                    continue
                rows.append([ix1 - x1, iy1 - y1, ix2 - x1, iy2 - y1, p.conf])
            out.append(_Result(rows))
        return out


def scene():
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    people = [
        Person(100, 100, 160, 260, 0.9),  # inside the top-left tile only
        Person(600, 150, 680, 330, 0.8),  # on the vertical seam: both top tiles and the full frame see it
        Person(620, 340, 700, 390, 0.7),  # in all four tiles' overlap
        Person(200, 300, 260, 420, 0.6),  # on the horizontal seam, cut by the top-left tile
        Person(1240, 600, 1280, 710, 0.5),  # at the frame's right edge
        Person(900, 50, 912, 80, 0.3, tiles_only=True),  # small and far: only a tile resolves it
        Person(400, 500, 700, 700, 0.95, full_only=True),  # large: whole only in the full frame
        Person(1000, 500, 1060, 650, 0.12),  # weak: counted at conf 0.10, not at 0.35
        Person(800, 400, 850, 520, 0.03),  # below every cut
    ]
    grid = tile_grid(1280, 720, 2, 2, 0.2)
    return frame, people, FakeModel(frame, people, grid)


def test_tracker_counts_each_person_once_across_tiles_and_the_full_frame():
    frame, people, model = scene()
    tr = PersonTracker(model=model, model_name="fake.pt", conf=0.10, device="cpu")
    dets = tr.detect(frame)
    assert len(dets) == sum(1 for p in people if p.conf >= 0.10) == 8
    assert model.calls == [(1, 1280), (4, 1280)]  # the full frame, then the four tiles in one batch
    # one box per person, at the person's place
    for p in people:
        hits = [d for d in dets if abs(d.x1 - p.x1) <= 3 and abs(d.y1 - p.y1) <= 3 and abs(d.x2 - p.x2) <= 3]
        assert len(hits) == (1 if p.conf >= 0.10 else 0), p
    # the merged set is one box per person; the same set cut at Berkshire's conf: the weak one drops out
    strict = PersonTracker(model=model, model_name="fake.pt", conf=0.35, device="cpu")
    assert len(strict.detect(frame)) == sum(1 for p in people if p.conf >= 0.35) == 6
    assert tr.setup == "fake full@1280+2x2@1280 iou0.5 seam0.8 conf0.1 cpu"
    with pytest.raises(ValueError):
        PersonTracker(model=model, conf=0.0)


def test_tracker_without_tiles_is_the_plain_full_frame_pass():
    frame, people, model = scene()
    tr = PersonTracker(model=model, model_name="fake.pt", conf=0.10, device="cpu", tiles=(1, 1))
    assert len(tr.detect(frame)) == 7  # the tiles-only person is missed
    assert model.calls == [(1, 1280)]
    assert tr.setup == "fake full@1280 iou0.5 conf0.1 cpu"


def test_track_gives_every_counted_box_an_id_and_keeps_it_between_frames():
    frame, people, model = scene()
    tr = PersonTracker(model=model, model_name="fake.pt", conf=0.10, device="cpu")
    first = tr.track(frame)
    second = tr.track(frame)
    assert len(first) == len(second) == 8
    ids1 = sorted(d.track_id for d in first)
    ids2 = sorted(d.track_id for d in second)
    assert None not in ids1 and len(set(ids1)) == 8  # the 0.12 box has an id too (thresholds follow conf)
    assert ids1 == ids2
    tr.reset()
    assert min(d.track_id for d in tr.track(frame)) == 1  # ids restart with the window


def test_count_plan_spreads_the_counted_frames_over_the_window():
    assert count_plan(32, 6) == [0, 6, 12, 19, 25, 31]
    assert count_plan(32, 1) == [0]
    assert count_plan(4, 6) == [0, 1, 2, 3]
    assert count_plan(1, 6) == [0]
    assert count_plan(0, 6) == []
    plan = count_plan(32, config.COUNT_FRAMES)
    assert plan[0] == 0 and plan[-1] == 31 and len(plan) == config.COUNT_FRAMES
    assert max(b - a for a, b in zip(plan, plan[1:], strict=False)) <= 7  # never more than 3.5 s apart


class FakeTracker:
    """track() returns i+1 boxes on its i-th call and takes `cost` fake seconds."""

    def __init__(self, clock, cost=2.0):
        self.calls = 0
        self.clock, self.cost = clock, cost

    def track(self, frame):
        self.calls += 1
        self.clock.t += self.cost
        return [Detection(k + 1, 10 * k, 0, 10 * k + 5, 20, 0.5) for k in range(self.calls)]


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def frames(n=32, interval=0.5):
    return [(1000.0 + i * interval, np.full((8, 8, 3), i, np.uint8)) for i in range(n)]


def test_run_window_counts_the_plan_and_holds_boxes_in_between():
    clock = Clock()
    tr = FakeTracker(clock)
    win = run_window(tr, frames(), count_frames=6, clock=clock)
    assert win.counted == [0, 6, 12, 19, 25, 31] and win.counts == [1, 2, 3, 4, 5, 6]
    assert win.ids == {1, 2, 3, 4, 5, 6} and win.seconds == pytest.approx(12.0)
    assert len(win.per_frame) == 32 and win.planned_short
    # a frame between two counted ones carries the boxes of the last counted one
    assert [len(d) for _ts, _f, d in win.per_frame] == [1] * 6 + [2] * 6 + [3] * 7 + [4] * 6 + [5] * 6 + [6]
    assert win.per_frame[7][1][0, 0, 0] == 7  # the frame itself is the real one, not the counted one
    assert win.per_frame[7][2] is win.per_frame[6][2]


def test_run_window_stops_counting_at_the_deadline_but_always_counts_once():
    clock = Clock()
    tr = FakeTracker(clock, cost=2.0)
    # 5 s left: the first frame (2 s) is always counted, the second fits, the third would end past the deadline
    win = run_window(tr, frames(), count_frames=6, deadline=clock.t + 5.0, clock=clock)
    assert win.counted == [0, 6] and win.counts == [1, 2] and tr.calls == 2
    assert len(win.per_frame) == 32 and all(len(d) == 2 for _ts, _f, d in win.per_frame[6:])
    clock2 = Clock()
    tr2 = FakeTracker(clock2, cost=2.0)
    win2 = run_window(tr2, frames(), count_frames=6, deadline=clock2.t - 10, clock=clock2)  # already late
    assert win2.counted == [0] and win2.counts == [1]
    assert run_window(tr2, [], count_frames=6, clock=clock2).counts == []


def test_count_deadline_keeps_the_later_halls_share():
    now = 1000.0
    deadline = now + 150.0
    assert sample.count_deadline(deadline, halls_left=2, now=now) == pytest.approx(
        deadline - 2 * config.HALL_RESERVE_S - sample.CLIP_RESERVE_S
    )
    assert sample.count_deadline(deadline, halls_left=0, now=now) == pytest.approx(deadline - sample.CLIP_RESERVE_S)
    assert sample.count_deadline(now + 1.0, halls_left=3, now=now) == now  # never in the past


def test_sample_one_counts_a_subset_of_the_window_and_records_it(monkeypatch):
    from datetime import UTC, datetime

    from umass_cams import streams
    from umass_cams.streams import by_youtube_id

    class Grabber:
        def grab(self, stream, **kw):
            return streams.GrabResult(stream=stream, engine="webkit", status="ok", frames=frames(32), seconds=20.0)

    class Tracker:
        model_name = "yolo11l"
        setup = "yolo11l full@1280+2x2@1280 iou0.5 conf0.1 cpu"

        def track(self, frame):
            return [Detection(k, 0, 0, 5, 5, 0.5) for k in range(int(frame[0, 0, 0]) % 3 + 70)]  # 70..72 people

    s = by_youtube_id("7wHgOpwHz8k")
    now = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
    doc = sample.sample_one(Grabber(), lambda stream: Tracker(), s, None, now, 16.0, 1e12, True, None, halls_left=2)
    assert doc["frames"] == config.COUNT_FRAMES and doc["clip_frames"] == 32
    assert len(doc["counts"]) == config.COUNT_FRAMES and doc["people"] in (70, 71, 72)
    assert doc["level"] == "busy" and doc["model"] == "yolo11l" and doc["detector"] == Tracker.setup
    assert doc["count_s"] >= 0 and doc["grab_s"] == 20.0 and "reason" not in doc
    assert sample.json_safe(doc)["ts"] == "2026-11-20T17:00:00Z"

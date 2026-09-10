import os
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from bob.vision import FaceEngine, FakeCamera, Person, Tracker
from bob.vision.faces import SFACE, YUNET, iou

MODELS = Path("models/vision")
HAVE_MODELS = (MODELS / YUNET).exists() and (MODELS / SFACE).exists()


# -- Person ----------------------------------------------------------------


@pytest.mark.parametrize(
    "bbox, cx, cy, size",
    [
        ((0, 0, 640, 480), 0.0, 0.0, 1.0),
        ((480, 360, 160, 120), 0.75, 0.75, 0.25),
        ((0, 0, 64, 48), -0.9, -0.9, 0.1),
        ((288, 216, 64, 48), 0.0, 0.0, 0.1),
    ],
)
def test_person_normalizes_center_and_size(bbox, cx, cy, size):
    person = Person.from_bbox(bbox, 640, 480, name="Ann", score=0.5)
    assert (person.cx, person.cy, person.size) == pytest.approx((cx, cy, size))
    assert person.bbox == bbox and person.name == "Ann" and person.emotion is None


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 10, 10)) == 0.0
    assert iou((0, 0, 10, 10), (5, 0, 10, 10)) == pytest.approx(50 / 150)


# -- Tracker ---------------------------------------------------------------


def face(name, bbox=(100, 100, 80, 80), score=0.5):
    return Person.from_bbox(bbox, 640, 480, name=name, score=score)


def test_tracker_commits_name_only_after_five_frame_majority():
    tracker = Tracker(clock=lambda: 0.0)
    for _ in range(4):
        assert tracker.update([face("Ann")])[0].name is None
    assert tracker.update([face("Ann")])[0].name == "Ann"
    assert len(tracker.slots) == 1


def test_tracker_majority_ignores_flicker_and_needs_majority():
    tracker = Tracker(clock=lambda: 0.0)
    out = [tracker.update([face(n)])[0].name for n in ["Ann", None, "Ann", "Bob", "Ann"]]
    assert out == [None, None, None, None, "Ann"]
    # A committed name sticks while the window has no other majority.
    assert tracker.update([face(None)])[0].name == "Ann"
    # Split window (2 Ann / 2 Bob / 1 None) has no majority -> last commitment stays.
    tracker2 = Tracker(clock=lambda: 0.0)
    for n in ["Ann", "Bob", "Ann", "Bob", None]:
        last = tracker2.update([face(n)])[0].name
    assert last is None


def test_tracker_matches_slots_by_iou_and_drops_lost_ones():
    tracker = Tracker(clock=lambda: 0.0, max_missed=1)
    left, right = (0, 0, 100, 100), (400, 300, 100, 100)
    for _ in range(5):
        out = tracker.update([face("Ann", left), face("Bob", right)])
    assert [p.name for p in out] == ["Ann", "Bob"]
    # Slightly moved box (IoU > 0.3) keeps its slot and name.
    out = tracker.update([face("Ann", (10, 10, 100, 100))])
    assert out[0].name == "Ann"
    tracker.update([])
    tracker.update([])
    assert tracker.slots == []
    # A far-away box is a fresh slot with no committed name.
    assert tracker.update([face("Ann", (500, 0, 50, 50))])[0].name is None


def test_tracker_seen_cooldown_uses_injected_clock():
    now = [1000.0]
    tracker = Tracker(clock=lambda: now[0])
    assert not tracker.seen_recently("Ann")
    tracker.mark_seen("Ann")
    assert tracker.seen_recently("Ann")
    now[0] += 599
    assert tracker.seen_recently("Ann")
    now[0] += 2
    assert not tracker.seen_recently("Ann")
    assert tracker.seen_recently("Ann", window_s=1000)
    assert not tracker.seen_recently("Bob")


# -- FakeCamera ------------------------------------------------------------


def test_fake_camera_cycles_frames():
    assert FakeCamera().latest() is None
    a, b = np.zeros((2, 2, 3), np.uint8), np.ones((2, 2, 3), np.uint8)
    cam = FakeCamera([a, b]).open()
    assert [cam.latest() is a, cam.latest() is b, cam.read() is a] == [True, True, True]
    cam.close()


# -- FaceEngine without cv2 (fake detector/embedder) ---------------------------


class FakeVision:
    """Stands in for imread/YuNet/SFace: the file name decides the face and its embedding."""

    def __init__(self, monkeypatch):
        monkeypatch.setattr(
            FaceEngine, "_read_image", lambda _engine, path: np.full((480, 640, 3), self.seed(path))
        )
        monkeypatch.setattr(FaceEngine, "detect_faces", self.detect_faces)
        monkeypatch.setattr(FaceEngine, "embed", self.embed)

    @staticmethod
    def seed(path):
        """ "<person>_<variant>.jpg" -> person*10 + variant (a=1, b=2, ...); "blank" -> 0 (no face)."""
        stem = Path(path).stem
        if stem == "blank":
            return 0
        person, variant = stem.split("_")
        return int(person) * 10 + ord(variant[0]) - ord("a") + 1

    @staticmethod
    def detect_faces(engine, frame):
        if frame[0, 0, 0] == 0:
            return np.zeros((0, 15), np.float32)
        row = np.zeros(15, np.float32)
        row[:4] = (200, 100, 120, 160)
        row[-1] = 0.95
        return row[np.newaxis, :]

    @staticmethod
    def embed(engine, frame, face):
        seed = int(frame[0, 0, 0])
        vec = np.random.default_rng(seed // 10).standard_normal(128).astype(np.float32)  # per person
        vec += np.random.default_rng(seed).standard_normal(128).astype(np.float32) * 0.2  # per photo
        return vec / np.linalg.norm(vec)


def test_enroll_dir_round_trip_and_detect(tmp_path, monkeypatch):
    FakeVision(monkeypatch)
    people = tmp_path / "people"
    for name, files in {
        "Ann": ["10_a.jpg", "10_b.png", "blank.jpg"],
        "Bob": ["40_a.jpg"],
        "Empty": [],
    }.items():
        (people / name).mkdir(parents=True)
        for f in files:
            (people / name / f).write_bytes(b"x")
    (people / "Ann" / "notes.txt").write_text("ignored")
    log = []
    engine = FaceEngine(tmp_path / "models")
    enrolled = engine.enroll_dir(people, log=log.append)

    assert sorted(enrolled) == ["Ann", "Bob"]
    assert any("no face" in m and "blank.jpg" in m for m in log)
    assert "Ann: 2/3 images used" in log and "Bob: 1/1 images used" in log and "Empty: 0/0 images used" in log
    for vec in enrolled.values():
        assert vec.shape == (128,) and np.linalg.norm(vec) == pytest.approx(1.0, abs=1e-5)

    saved = np.load(people / "embeddings.npz")
    assert list(saved["names"]) == ["Ann", "Bob"] and saved["matrix"].shape == (2, 128)

    fresh = FaceEngine(tmp_path / "models", threshold=0.5)
    loaded = fresh.load_embeddings(people / "embeddings.npz")
    assert sorted(loaded) == ["Ann", "Bob"]
    np.testing.assert_allclose(fresh.matrix, engine.matrix, atol=1e-6)

    frame_ann = np.full((480, 640, 3), 103)  # an unseen third photo of person 10 ("Ann")
    frame_bob = np.full((480, 640, 3), 402)
    frame_stranger = np.full((480, 640, 3), 991)
    (p,) = fresh.detect(frame_ann)
    assert p.name == "Ann" and p.score > 0.5 and p.bbox == (200, 100, 120, 160)
    assert p.cx == pytest.approx((260 / 640) * 2 - 1) and p.size == pytest.approx(160 / 480)
    assert fresh.detect(frame_bob)[0].name == "Bob"
    stranger = fresh.detect(frame_stranger)[0]
    assert stranger.name is None and stranger.score < 0.5
    assert fresh.detect(np.zeros((480, 640, 3), np.uint8)) == []


# -- Real models -----------------------------------------------------------------


@pytest.fixture
def real_engine():
    pytest.importorskip("cv2")
    if not HAVE_MODELS:
        pytest.skip("models/vision missing; run scripts/fetch_models.py")
    return FaceEngine(MODELS).load()


def test_detect_blank_frame_is_empty(real_engine):
    assert real_engine.detect(np.zeros((480, 640, 3), np.uint8)) == []
    assert real_engine.detect(np.full((240, 320, 3), 128, np.uint8)) == []


PORTRAITS = {
    # Public-domain 19th-century portraits on Wikimedia Commons (Special:FilePath resolves the file).
    "lincoln_1": "Abraham_Lincoln_November_1863.jpg",
    "lincoln_2": "Abraham_Lincoln_seated,_Feb_9,_1864.jpg",
    "douglass": "Frederick_Douglass_(circa_1879).jpg",
}


def _fetch_portrait(name: str, dest: Path) -> Path:
    url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(name) + "?width=640"
    request = urllib.request.Request(url, headers={"User-Agent": "bob-tests/1 (face recognition test)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        dest.write_bytes(response.read())
    return dest


@pytest.mark.skipif(
    os.environ.get("BOB_NET_TESTS") != "1", reason="set BOB_NET_TESTS=1 to download portraits"
)
def test_recognizes_second_photo_of_enrolled_person_not_other(real_engine, tmp_path):
    cv2 = pytest.importorskip("cv2")
    people = tmp_path / "people" / "Lincoln"
    people.mkdir(parents=True)
    _fetch_portrait(PORTRAITS["lincoln_1"], people / "1863.jpg")
    lincoln_2 = _fetch_portrait(PORTRAITS["lincoln_2"], tmp_path / "lincoln_1864.jpg")
    douglass = _fetch_portrait(PORTRAITS["douglass"], tmp_path / "douglass_1879.jpg")

    enrolled = real_engine.enroll_dir(tmp_path / "people")
    assert list(enrolled) == ["Lincoln"]
    e_a = real_engine.embed_file(lincoln_2)
    e_b = real_engine.embed_file(douglass)
    assert e_a is not None and e_b is not None
    same, other = float(enrolled["Lincoln"] @ e_a), float(enrolled["Lincoln"] @ e_b)
    print(f"\ncosine Lincoln-vs-Lincoln={same:.4f}  Lincoln-vs-Douglass={other:.4f}")
    assert same > other
    assert same >= real_engine.threshold > other

    (p_same,) = real_engine.detect(cv2.imread(str(lincoln_2)))
    (p_other,) = real_engine.detect(cv2.imread(str(douglass)))
    assert p_same.name == "Lincoln" and p_same.score == pytest.approx(same, abs=1e-4)
    assert p_other.name is None and p_other.score == pytest.approx(other, abs=1e-4)

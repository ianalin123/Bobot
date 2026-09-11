"""Face detection (YuNet), recognition (SFace) and optional expression (MobileFaceNet FER).

`cv2` is imported lazily inside `FaceEngine.load()` so this module (and `Person`/`Tracker`)
work without OpenCV installed. Only embeddings are stored; images are never written.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

YUNET = "face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"
FER = "facial_expression_recognition_mobilefacenet_2022july.onnx"
EMOTIONS = ("angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

BBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class Person:
    """One detected face. `cx`, `cy` are -1..1 (positive = right/down); `size` = bbox height / frame height."""

    name: str | None
    score: float
    bbox: BBox
    cx: float
    cy: float
    size: float
    emotion: str | None = None

    @classmethod
    def from_bbox(
        cls,
        bbox: BBox,
        frame_w: int,
        frame_h: int,
        name: str | None = None,
        score: float = 0.0,
        emotion: str | None = None,
    ) -> "Person":
        x, y, w, h = bbox
        cx = (x + w / 2) / frame_w * 2 - 1
        cy = (y + h / 2) / frame_h * 2 - 1
        return cls(name=name, score=score, bbox=bbox, cx=cx, cy=cy, size=h / frame_h, emotion=emotion)


def iou(a: BBox, b: BBox) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


def _bbox_of(face: np.ndarray) -> BBox:
    x, y, w, h = face[:4]
    return int(round(float(x))), int(round(float(y))), int(round(float(w))), int(round(float(h)))


class FaceEngine:
    """YuNet detector + SFace recognizer over a set of enrolled embeddings."""

    def __init__(
        self, models_dir: str | Path = "models/vision", threshold: float = 0.363, emotion: bool = False
    ):
        self.models_dir = Path(models_dir)
        self.threshold = threshold
        self.emotion = emotion
        self.detector: Any = None
        self.recognizer: Any = None
        self.fer: Any = None
        self.names: list[str] = []
        self.matrix: np.ndarray = np.zeros((0, 128), dtype=np.float32)
        self._input_size: tuple[int, int] | None = None

    # -- models -----------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self.detector is not None and self.recognizer is not None

    def load(self) -> "FaceEngine":
        import cv2

        yunet, sface = self.models_dir / YUNET, self.models_dir / SFACE
        for path in (yunet, sface):
            if not path.exists():
                raise FileNotFoundError(f"missing model {path}; run scripts/fetch_models.py")
        self.detector = cv2.FaceDetectorYN.create(str(yunet), "", (320, 320), 0.9, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(sface), "")
        fer = self.models_dir / FER
        if self.emotion and fer.exists():
            self.fer = cv2.dnn.readNet(str(fer))
        return self

    # -- primitives -------------------------------------------------------

    def detect_faces(self, frame: np.ndarray) -> np.ndarray:
        """Raw YuNet rows: x, y, w, h, 5 landmark pairs, score. Shape (N, 15)."""
        if self.detector is None:
            raise RuntimeError("FaceEngine.load() first")
        h, w = frame.shape[:2]
        if self._input_size != (w, h):
            self.detector.setInputSize((w, h))
            self._input_size = (w, h)
        _, faces = self.detector.detect(frame)
        if faces is None:
            return np.zeros((0, 15), dtype=np.float32)
        return np.asarray(faces, dtype=np.float32)

    def embed(self, frame: np.ndarray, face: np.ndarray) -> np.ndarray:
        """L2-normalized 128-d SFace embedding for one YuNet row."""
        aligned = self.recognizer.alignCrop(frame, face)
        return _normalize(self.recognizer.feature(aligned))

    def classify_emotion(self, frame: np.ndarray, face: np.ndarray) -> str | None:
        if self.fer is None:
            return None
        import cv2

        aligned = self.recognizer.alignCrop(frame, face)
        rgb = cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - 0.5) / 0.5
        blob = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]
        self.fer.setInput(blob)
        out = self.fer.forward()
        return EMOTIONS[int(np.argmax(out))]

    def match(self, embedding: np.ndarray) -> tuple[str | None, float]:
        """Best cosine match among enrolled people: (name or None, score)."""
        if len(self.names) == 0:
            return None, 0.0
        scores = self.matrix @ _normalize(embedding)
        i = int(np.argmax(scores))
        best = float(scores[i])
        return (self.names[i] if best >= self.threshold else None), best

    # -- enrollment -------------------------------------------------------

    def _read_image(self, path: Path, max_side: int = 800) -> np.ndarray | None:
        """Read a photo, shrinking big ones: YuNet misses faces that fill a 12-megapixel frame,
        and the live camera is 640 px wide anyway."""
        import cv2

        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            return None
        h, w = image.shape[:2]
        scale = max_side / max(h, w)
        if scale < 1.0:
            image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        return image

    def embed_image(self, image: np.ndarray) -> np.ndarray | None:
        """Embedding of the largest face in a frame, or None."""
        faces = self.detect_faces(image)
        if len(faces) == 0:
            return None
        biggest = faces[int(np.argmax(faces[:, 2] * faces[:, 3]))]
        return self.embed(image, biggest)

    def embed_file(self, path: Path) -> np.ndarray | None:
        """Embedding of the largest face in an image file, or None if no face is found."""
        image = self._read_image(path)
        if image is None:
            return None
        return self.embed_image(image)

    def embed_file_augmented(self, path: Path) -> list[np.ndarray]:
        """Embeddings of the photo plus mild variants (mirror, darker, brighter, small tilts) so one
        portrait covers more lighting and head angles. Variants where no face is found are skipped."""
        import cv2

        image = self._read_image(path)
        if image is None:
            return []
        h, w = image.shape[:2]
        variants = [image, cv2.flip(image, 1)]
        variants.append(cv2.convertScaleAbs(image, alpha=0.6, beta=-10))  # darker
        variants.append(cv2.convertScaleAbs(image, alpha=1.3, beta=25))  # brighter
        for angle in (-12, 12):
            m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            variants.append(cv2.warpAffine(image, m, (w, h), borderMode=cv2.BORDER_REPLICATE))
        out = []
        for v in variants:
            vec = self.embed_image(v)
            if vec is not None:
                out.append(vec)
        return out

    def enroll_dir(
        self,
        people_dir: str | Path,
        out_path: str | Path | None = None,
        log: Callable[[str], None] | None = None,
    ) -> dict[str, np.ndarray]:
        """Mean-normalized embedding per `people/<Name>/*.jpg|png`; writes `<people>/embeddings.npz`."""
        people_dir = Path(people_dir)
        out_path = Path(out_path) if out_path is not None else people_dir / "embeddings.npz"
        say = log or (lambda _msg: None)
        people: dict[str, np.ndarray] = {}
        for person_dir in sorted(p for p in people_dir.iterdir() if p.is_dir()):
            images = sorted(p for p in person_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
            vectors = []
            for image in images:
                vecs = self.embed_file_augmented(image)
                if not vecs:
                    say(f"warning: no face found in {image}")
                    continue
                vectors.extend(vecs)
            used = sum(1 for image in images if self.embed_file(image) is not None)
            say(f"{person_dir.name}: {used}/{len(images)} images used ({len(vectors)} views)")
            if vectors:
                people[person_dir.name] = _normalize(np.mean(np.stack(vectors), axis=0))
        self._set_people(people)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(out_path, names=np.array(self.names, dtype=str), matrix=self.matrix)
        return people

    def load_embeddings(self, path: str | Path) -> dict[str, np.ndarray]:
        data = np.load(Path(path), allow_pickle=False)
        names = [str(n) for n in data["names"]]
        matrix = np.asarray(data["matrix"], dtype=np.float32)
        people = {name: _normalize(row) for name, row in zip(names, matrix)}
        self._set_people(people)
        return people

    def _set_people(self, people: dict[str, np.ndarray]) -> None:
        self.names = list(people)
        if people:
            self.matrix = np.stack([_normalize(people[n]) for n in self.names]).astype(np.float32)
        else:
            self.matrix = np.zeros((0, 128), dtype=np.float32)

    # -- per-frame --------------------------------------------------------

    def detect(self, frame: np.ndarray) -> list[Person]:
        """Every face in the frame with its best match (name None when below threshold)."""
        frame_h, frame_w = frame.shape[:2]
        persons: list[Person] = []
        for face in self.detect_faces(frame):
            name, score = self.match(self.embed(frame, face)) if len(self.names) else (None, 0.0)
            emotion = self.classify_emotion(frame, face) if self.fer is not None else None
            persons.append(
                Person.from_bbox(_bbox_of(face), frame_w, frame_h, name=name, score=score, emotion=emotion)
            )
        return persons


@dataclass
class _Slot:
    bbox: BBox
    votes: deque
    committed: str | None = None
    missed: int = 0


class Tracker:
    """Smooths identities over frames: IoU slot matching, N-frame majority vote, greeting cooldown."""

    def __init__(
        self,
        votes: int = 5,
        iou_threshold: float = 0.3,
        max_missed: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.votes = votes
        self.iou_threshold = iou_threshold
        self.max_missed = max_missed
        self.clock = clock
        self.slots: list[_Slot] = []
        self._seen: dict[str, float] = {}

    def update(self, persons: list[Person]) -> list[Person]:
        """Assign each detection to a slot; return persons with the slot's committed name (None until voted)."""
        free = list(self.slots)
        out: list[Person] = []
        for person in persons:
            best, best_iou = None, self.iou_threshold
            for slot in free:
                score = iou(slot.bbox, person.bbox)
                if score > best_iou:
                    best, best_iou = slot, score
            if best is None:
                best = _Slot(bbox=person.bbox, votes=deque(maxlen=self.votes))
                self.slots.append(best)
            else:
                free.remove(best)
            best.bbox = person.bbox
            best.missed = 0
            best.votes.append(person.name)
            if len(best.votes) >= self.votes:
                winner, count = Counter(best.votes).most_common(1)[0]
                if count * 2 > len(best.votes):
                    best.committed = winner
            out.append(replace(person, name=best.committed))
        for slot in free:
            slot.missed += 1
        self.slots = [s for s in self.slots if s.missed <= self.max_missed]
        return out

    def mark_seen(self, name: str) -> None:
        self._seen[name] = self.clock()

    def seen_recently(self, name: str, window_s: float = 600) -> bool:
        last = self._seen.get(name)
        return last is not None and (self.clock() - last) < window_s

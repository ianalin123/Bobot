"""Camera capture and face recognition (YuNet + SFace). cv2 is imported lazily by each module."""

from .camera import Camera, FakeCamera
from .faces import FaceEngine, Person, Tracker

__all__ = ["Camera", "FakeCamera", "FaceEngine", "Person", "Tracker"]

"""Local video library and per-video, normalized parking-zone configuration."""

import hashlib
import json
import math
import os
import re
import threading
import uuid
from pathlib import Path

import cv2
import numpy as np

from detector import ROOT, DEFAULT_VIDEO, load_rois

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
PRESETS = {"responsive": (2, 4), "balanced": (3, 8), "steady": (5, 12)}


class ConflictError(ValueError):
    pass


def validate_zones(zones):
    if not isinstance(zones, list) or len(zones) > 200:
        raise ValueError("Supply up to 200 parking zones.")
    cleaned = []
    for zone in zones:
        if not isinstance(zone, list) or len(zone) != 4:
            raise ValueError("Each space needs four corners.")
        for point in zone:
            if (not isinstance(point, list) or len(point) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in point)):
                raise ValueError("Zone coordinates must be finite numbers between 0 and 1.")
        polygon = np.asarray(zone, dtype=np.float32)
        if not cv2.isContourConvex(polygon) or cv2.contourArea(polygon) < 0.00001:
            raise ValueError("Draw a non-crossing, convex space with four distinct corners.")
        cleaned.append(polygon.astype(float).tolist())
    return cleaned


def inspect_video(path):
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError("This video cannot be decoded. Try an MP4 encoded with H.264.")
        ok, frame = capture.read()
        if not ok:
            raise ValueError("The video has no readable first frame.")
        height, width = frame.shape[:2]
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(fps) or not 0 < fps <= 240 or not math.isfinite(count) or count < 1:
            raise ValueError("The video has invalid timing information.")
        if max(width, height) > 4096:
            raise ValueError("Use a video with dimensions no larger than 4096 pixels.")
        ok, preview = cv2.imencode(".jpg", frame)
        if not ok:
            raise ValueError("The video preview could not be generated.")
        return {"width": width, "height": height, "duration_seconds": count / fps}, preview.tobytes()
    finally:
        capture.release()


class SourceStore:
    def __init__(self, directory=ROOT / "data"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        if not self._path("sample").exists():
            metadata, preview = inspect_video(DEFAULT_VIDEO)
            reference = cv2.imread(str(ROOT / "ROI_Reference.png"))
            if reference is None:
                raise ValueError("ROI reference image is missing.")
            h, w = reference.shape[:2]
            zones = [(polygon / np.array([w, h])).tolist() for polygon in load_rois()]
            self._write({"id": "sample", "name": "Sample parking video", "filename": None,
                         "zones": zones, "revision": 1, "smoothing": "balanced", **metadata})
            (self.directory / "sample.jpg").write_bytes(preview)

    def _path(self, source_id):
        if not isinstance(source_id, str) or not re.fullmatch(r"sample|[a-f0-9]{64}", source_id):
            raise ValueError("Unknown video.")
        return self.directory / f"{source_id}.json"

    def _write(self, record):
        target = self._path(record["id"])
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(record, allow_nan=False), encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, source_id):
        with self.lock:
            path = self._path(source_id)
            if not path.is_file():
                raise ValueError("Unknown video.")
            return json.loads(path.read_text(encoding="utf-8"))

    def video_path(self, record):
        return DEFAULT_VIDEO if record["id"] == "sample" else self.directory / record["filename"]

    def list(self):
        with self.lock:
            records = [self.get(path.stem) for path in sorted(self.directory.glob("*.json"))
                       if path.stem != "active"]
            return sorted(records, key=lambda item: (item["id"] != "sample", item["name"].lower()))

    def active_id(self):
        try:
            value = json.loads((self.directory / "active.json").read_text(encoding="utf-8"))["id"]
            self.get(value)
            return value
        except (OSError, ValueError, KeyError):
            return "sample"

    def set_active(self, source_id):
        self.get(source_id)
        target = self.directory / "active.json"
        temporary = self.directory / "active.tmp"
        with self.lock:
            temporary.write_text(json.dumps({"id": source_id}), encoding="utf-8")
            os.replace(temporary, target)

    def upload(self, storage):
        name = (storage.filename or "").replace("\\", "/").split("/")[-1][:160]
        extension = Path(name).suffix.lower()
        if extension not in VIDEO_EXTENSIONS:
            raise ValueError("Choose an MP4, MOV, AVI, MKV, or WebM video.")
        temporary = self.directory / f"upload-{uuid.uuid4().hex}{extension}"
        try:
            digest = hashlib.sha256()
            size = 0
            with temporary.open("wb") as output:
                while chunk := storage.stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise ValueError("Videos must be 500 MB or smaller.")
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise ValueError("The selected video is empty.")
            source_id = digest.hexdigest()
            with self.lock:
                if self._path(source_id).exists():
                    return self.get(source_id)
                metadata, preview = inspect_video(temporary)
                filename = source_id + extension
                record = {"id": source_id, "name": name, "filename": filename,
                          "zones": [], "revision": 1, "smoothing": "balanced", **metadata}
                os.replace(temporary, self.directory / filename)
                (self.directory / f"{source_id}.jpg").write_bytes(preview)
                self._write(record)
                return record
        finally:
            temporary.unlink(missing_ok=True)

    def save_zones(self, source_id, zones, revision, smoothing):
        zones = validate_zones(zones)
        if not isinstance(smoothing, str) or smoothing not in PRESETS:
            raise ValueError("Choose a valid detection stability preset.")
        with self.lock:
            record = self.get(source_id)
            if type(revision) is not int or revision != record["revision"]:
                raise ConflictError("These zones changed in another tab. Reopen the editor before saving.")
            record.update(zones=zones, smoothing=smoothing, revision=revision + 1)
            self._write(record)
            return record

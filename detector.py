"""Shared parking inference for the desktop app and localhost dashboard."""

import os
import pickle
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_VIDEO = ROOT / "input_video" / "parking_space.mp4"
DEFAULT_MODEL = ROOT / "yolov8n.pt"
DEFAULT_ROIS = ROOT / "Space_ROIs"
VEHICLE_CLASSES = [2, 3, 5, 7]  # COCO: car, motorcycle, bus, truck.

os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".cache" / "ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))


class _ROIUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError("Parking zones must contain only lists and coordinates.")


def load_rois(path=DEFAULT_ROIS):
    """Read the existing coordinate-only file without permitting pickle objects."""
    with Path(path).open("rb") as source:
        zones = _ROIUnpickler(source).load()
    if not isinstance(zones, list) or not zones:
        raise ValueError("No parking zones configured. Run ParkingSpaceSelection.py first.")
    result = []
    for zone in zones:
        polygon = np.asarray(zone, dtype=np.float64)
        if polygon.shape != (4, 2) or not np.isfinite(polygon).all():
            raise ValueError("Each parking zone must contain four finite (x, y) points.")
        if (polygon < 0).any() or (polygon > 100000).any():
            raise ValueError("Parking zone coordinates are outside the supported range.")
        polygon = polygon.astype(np.int32)
        if abs(cv2.contourArea(polygon)) < 1:
            raise ValueError("A parking zone has no area.")
        result.append(polygon)
    return result


def occupied_spaces(zones, detection_points):
    return [
        any(cv2.pointPolygonTest(zone, (float(x), float(y)), False) >= 0
            for x, y in detection_points)
        for zone in zones
    ]


class OccupancySmoother:
    """Require consecutive evidence; tolerate longer missed detections on departure."""

    def __init__(self, occupied_frames=3, available_frames=8):
        self.occupied_frames = occupied_frames
        self.available_frames = available_frames
        self.reset()

    def reset(self):
        self.stable = []
        self.candidates = []
        self.streaks = []

    def update(self, observations):
        if len(self.stable) != len(observations):
            self.stable = [None] * len(observations)
            self.candidates = [None] * len(observations)
            self.streaks = [0] * len(observations)
        for i, value in enumerate(observations):
            if value == self.stable[i]:
                self.streaks[i] = 0
                self.candidates[i] = None
                continue
            self.streaks[i] = self.streaks[i] + 1 if value == self.candidates[i] else 1
            self.candidates[i] = value
            required = self.occupied_frames if value or self.stable[i] is None else self.available_frames
            if self.streaks[i] >= required:
                self.stable[i] = value
                self.streaks[i] = 0
                self.candidates[i] = None
        return self.stable.copy()


class ParkingDetector:
    def __init__(self, model_path=DEFAULT_MODEL, roi_path=DEFAULT_ROIS, confidence=0.35):
        model_path = Path(model_path)
        if not model_path.is_file():
            raise FileNotFoundError(f"YOLO model not found: {model_path}")
        self.zones = load_rois(roi_path)
        reference = cv2.imread(str(ROOT / "ROI_Reference.png"))
        if reference is None:
            raise FileNotFoundError("ROI_Reference.png is missing or unreadable.")
        self.reference_height, self.reference_width = reference.shape[:2]
        import torch
        from ultralytics import YOLO

        torch.set_num_threads(min(4, os.cpu_count() or 1))
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = YOLO(str(model_path))
        self.model.to(self.device)
        self.confidence = confidence
        self.smoother = OccupancySmoother()

    def configure(self, zones, width, height, occupied_frames=3, available_frames=8):
        self.zones = [np.asarray(zone, dtype=np.float64) * [width, height] for zone in zones]
        self.reference_width, self.reference_height = width, height
        self.smoother = OccupancySmoother(occupied_frames, available_frames)

    def analyze(self, frame):
        height, width = frame.shape[:2]
        scale = np.array([width / self.reference_width, height / self.reference_height])
        zones = [(zone * scale).astype(np.int32) for zone in self.zones]
        result = self.model.predict(
            frame, conf=self.confidence, classes=VEHICLE_CLASSES,
            device=self.device, verbose=False, imgsz=640,
        )[0]
        boxes = result.boxes.xyxy.cpu().tolist()
        classes = result.boxes.cls.cpu().tolist()
        centers = [((x1 + x2) / 2, (y1 + y2) / 2) for x1, y1, x2, y2 in boxes]
        occupied = self.smoother.update(occupied_spaces(zones, centers))
        def zone_color(value):
            return (100, 180, 225) if value is None else ((83, 91, 232) if value else (126, 190, 35))
        overlay = frame.copy()
        for zone, is_occupied in zip(zones, occupied):
            cv2.fillPoly(overlay, [zone], zone_color(is_occupied))
        annotated = cv2.addWeighted(overlay, 0.25, frame, 0.75, 0)
        for index, (zone, is_occupied) in enumerate(zip(zones, occupied), 1):
            color = zone_color(is_occupied)
            cv2.polylines(annotated, [zone], True, color, 2, cv2.LINE_AA)
            center = tuple(int(value) for value in zone.mean(axis=0))
            cv2.circle(annotated, center, 13, (35, 34, 24), -1, cv2.LINE_AA)
            cv2.putText(annotated, str(index), (center[0] - (9 if index > 9 else 5), center[1] + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
        for box, cls in zip(boxes, classes):
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (239, 220, 114), 1)
            cv2.putText(annotated, result.names[int(cls)], (x1, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
        count = sum(value is True for value in occupied)
        return annotated, {
            "total": len(zones), "occupied": count, "available": sum(value is False for value in occupied),
            "pending": sum(value is None for value in occupied),
            "spaces": [{"id": i, "occupied": value} for i, value in enumerate(occupied, 1)],
        }

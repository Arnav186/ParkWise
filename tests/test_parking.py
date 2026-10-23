"""Regression checks that exercise real geometry and HTTP behavior without YOLO."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from app import ParkingStream, create_app
from detector import ParkingDetector, OccupancySmoother, occupied_spaces


def rectangle(left, top, right, bottom):
    return np.array([(left, top), (right, top), (right, bottom), (left, bottom)],
                    dtype=np.int32)


class FakeTensor:
    def __init__(self, values):
        self.values = values

    def cpu(self):
        return self

    def tolist(self):
        return self.values


class OccupancyTests(unittest.TestCase):
    def setUp(self):
        self.zones = [rectangle(0, 0, 20, 20), rectangle(30, 0, 50, 20)]

    def test_no_detections_leaves_all_spaces_available(self):
        self.assertEqual(occupied_spaces(self.zones, []), [False, False])

    def test_multiple_detections_in_one_space_count_it_once(self):
        occupied = occupied_spaces(self.zones, [(5, 5), (5, 5), (15, 15), (70, 5)])
        self.assertEqual(occupied, [True, False])
        self.assertEqual(sum(occupied), 1)

    def test_boundary_and_corner_count_as_inside(self):
        for point in [(0, 10), (20, 10), (0, 0), (20, 20)]:
            with self.subTest(point=point):
                self.assertEqual(occupied_spaces(self.zones, [point]), [True, False])

    def test_point_just_outside_is_not_rounded_into_space(self):
        self.assertEqual(occupied_spaces(self.zones, [(20.01, 10)]), [False, False])


class DetectorTests(unittest.TestCase):
    def test_vehicle_filter_and_scaled_rois_keep_pedestrian_space_available(self):
        # Bypass model loading while running the actual analyze/annotation code.
        detector = ParkingDetector.__new__(ParkingDetector)
        detector.zones = [rectangle(0, 0, 20, 20), rectangle(30, 0, 50, 20)]
        detector.reference_width = 100
        detector.reference_height = 50
        detector.confidence = 0.35
        detector.smoother = OccupancySmoother(1, 1)
        detector.device = "cpu"
        candidates = [([24, 4, 36, 16], 2), ([64, 4, 76, 16], 0)]

        def predict(frame, **options):
            self.assertEqual(set(options["classes"]), {2, 3, 5, 7})
            selected = [(box, cls) for box, cls in candidates if cls in options["classes"]]
            return [SimpleNamespace(
                boxes=SimpleNamespace(
                    xyxy=FakeTensor([box for box, _ in selected]),
                    cls=FakeTensor([cls for _, cls in selected]),
                ),
                names={0: "person", 2: "car"},
            )]

        detector.model = SimpleNamespace(predict=Mock(side_effect=predict))
        source = np.zeros((100, 200, 3), dtype=np.uint8)
        annotated, counts = detector.analyze(source)
        self.assertEqual(counts["total"], 2)
        self.assertEqual(counts["occupied"], 1)
        self.assertEqual(counts["available"], 1)
        self.assertEqual(counts["spaces"], [
            {"id": 1, "occupied": True}, {"id": 2, "occupied": False},
        ])
        self.assertEqual(annotated.shape, source.shape)
        self.assertFalse(np.any(source), "Annotation must not modify the input frame")
        detector.model.predict.assert_called_once()


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.stream = ParkingStream()
        self.application = create_app(self.stream)
        self.application.config["TESTING"] = True
        self.client = self.application.test_client()

    def test_status_reports_loading_without_starting_inference(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        state = response.get_json()
        self.assertEqual(state["status"], "loading")
        self.assertIsNone(state["occupied"])
        self.assertIsNone(state["available"])
        self.assertIsNone(self.stream.thread)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_invalid_controls_return_json_client_errors(self):
        for body in [{}, [], "pause", {"action": "delete"}, {"action": None}]:
            with self.subTest(body=body):
                response = self.client.post("/api/control", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
        response = self.client.post("/api/control", data="{broken", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_controls_during_loading_return_conflict_without_mutation(self):
        initial = self.stream.snapshot()
        for action in ["pause", "resume", "restart"]:
            with self.subTest(action=action):
                response = self.client.post("/api/control", json={"action": action})
                self.assertEqual(response.status_code, 409)
                self.assertIn("not ready", response.get_json()["error"])
                self.assertEqual(self.stream.snapshot(), initial)

    def test_pause_resume_and_restart_update_running_state(self):
        self.stream.state["status"] = "running"
        response = self.client.post("/api/control", json={"action": "pause"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "paused")
        self.assertTrue(self.stream.paused)
        response = self.client.post("/api/control", json={"action": "resume"})
        self.assertEqual(response.get_json()["status"], "running")
        self.assertFalse(self.stream.paused)
        response = self.client.post("/api/control", json={"action": "restart"})
        self.assertEqual(response.get_json()["status"], "running")
        self.assertTrue(self.stream.restart_requested)

    def test_snapshot_does_not_expose_mutable_state(self):
        self.stream.state["spaces"] = [{"id": 1, "occupied": True}]
        snapshot = self.stream.snapshot()
        snapshot["spaces"][0]["occupied"] = False
        self.assertTrue(self.stream.snapshot()["spaces"][0]["occupied"])

    def test_missing_video_sets_error_before_loading_model(self):
        path = Path(__file__).with_name("missing-test-video.mp4")
        self.assertFalse(path.exists())
        stream = ParkingStream(path)
        with patch("app.ParkingDetector") as detector, self.assertLogs("app", level="ERROR"):
            stream._run()
        detector.assert_not_called()
        state = stream.snapshot()
        self.assertEqual(state["status"], "error")
        self.assertIn("Sample video not found", state["error"])
        client = create_app(stream).test_client()
        response = client.get("/video_feed")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"], state["error"])
        self.assertEqual(list(stream.frames()), [])

    def test_unreadable_video_releases_capture_without_loading_model(self):
        # This source exists but is not a video; keep the decoder deterministic.
        stream = ParkingStream(Path(__file__))
        capture = Mock()
        capture.isOpened.return_value = False
        with patch("app.cv2.VideoCapture", return_value=capture), \
                patch("app.ParkingDetector") as detector, \
                self.assertLogs("app", level="ERROR"):
            stream._run()
        detector.assert_not_called()
        capture.release.assert_called_once()
        self.assertEqual(stream.snapshot()["status"], "error")
        self.assertIn("could not be opened", stream.snapshot()["error"])


if __name__ == "__main__":
    unittest.main()

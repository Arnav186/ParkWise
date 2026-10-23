"""Upload, zone persistence, and temporal-stability regressions."""

import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

from app import ParkingStream, ParkingWorkspace, create_app
from detector import DEFAULT_VIDEO, ROOT, OccupancySmoother
from sources import SourceStore, ConflictError, validate_zones

ZONE = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]


class StabilityTests(unittest.TestCase):
    def test_startup_stays_unknown_until_confirmed(self):
        smoother = OccupancySmoother(3, 8)
        self.assertEqual(smoother.update([True, False]), [None, None])
        self.assertEqual(smoother.update([True, False]), [None, None])
        self.assertEqual(smoother.update([True, False]), [True, False])

    def test_short_dropout_does_not_free_an_occupied_space(self):
        smoother = OccupancySmoother(3, 8)
        for _ in range(3):
            smoother.update([True])
        for _ in range(7):
            self.assertEqual(smoother.update([False]), [True])
        self.assertEqual(smoother.update([True]), [True])
        for _ in range(7):
            self.assertEqual(smoother.update([False]), [True])
        self.assertEqual(smoother.update([False]), [False])

    def test_single_false_positive_does_not_occupy_available_space(self):
        smoother = OccupancySmoother(3, 8)
        for _ in range(3):
            smoother.update([False])
        self.assertEqual(smoother.update([True]), [False])
        self.assertEqual(smoother.update([False]), [False])
        self.assertEqual(smoother.update([True]), [False])
        self.assertEqual(smoother.update([True]), [False])
        self.assertEqual(smoother.update([True]), [True])

    def test_reset_does_not_leak_prior_video_state(self):
        smoother = OccupancySmoother(2, 4)
        smoother.update([True])
        smoother.update([True])
        smoother.reset()
        self.assertEqual(smoother.update([False]), [None])
        self.assertEqual(smoother.update([False]), [False])

    def test_each_zone_tracks_its_own_evidence(self):
        smoother = OccupancySmoother(2, 4)
        smoother.update([True, False])
        self.assertEqual(smoother.update([False, False]), [None, False])
        self.assertEqual(smoother.update([False, True]), [False, False])
        self.assertEqual(smoother.update([False, True]), [False, True])


class ZoneValidationTests(unittest.TestCase):
    def test_accepts_both_winding_orders_and_empty_configuration(self):
        self.assertEqual(len(validate_zones([ZONE, ZONE[::-1]])), 2)
        self.assertEqual(validate_zones([]), [])

    def test_rejects_crossed_flat_outside_and_nonfinite_zones(self):
        bad = [None, [ZONE] * 201, [[[0, 0], [1, 1], [1, 0], [0, 1]]],
               [[[0, 0], [0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]],
               [[[-0.1, 0.1], *ZONE[1:]]], [[[float("nan"), 0.1], *ZONE[1:]]],
               [[[True, 0.1], *ZONE[1:]]], [[ZONE[0], ZONE[1], ZONE[2]]]]
        for zones in bad:
            with self.subTest(zones=zones), self.assertRaises(ValueError):
                validate_zones(zones)


class SourceTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache" / "tests"
        directory.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temporary.cleanup)
        self.store = SourceStore(Path(self.temporary.name))
        self.stream = ParkingStream()
        self.app = create_app(self.stream, self.store)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def upload_sample(self, name="my parking.mp4"):
        with DEFAULT_VIDEO.open("rb") as handle:
            return self.store.upload(FileStorage(stream=handle, filename=name))

    def test_upload_is_decoded_and_has_no_inherited_zones(self):
        record = self.upload_sample("../../my parking.mp4")
        self.assertEqual(record["name"], "my parking.mp4")
        self.assertEqual(record["zones"], [])
        self.assertEqual(record["width"], 1214)
        self.assertTrue(self.store.video_path(record).is_file())
        with self.client.get(f"/api/sources/{record['id']}/preview") as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.store.get("sample")["zones"]), 18)

    def test_same_video_reupload_preserves_saved_zones(self):
        record = self.upload_sample()
        saved = self.store.save_zones(record["id"], [ZONE], 1, "steady")
        repeated = self.upload_sample("renamed.mp4")
        self.assertEqual(saved, repeated)
        self.assertEqual(len(self.store.list()), 2)

    def test_zones_and_active_source_survive_store_reload(self):
        record = self.upload_sample()
        saved = self.store.save_zones(record["id"], [ZONE], 1, "responsive")
        self.store.set_active(record["id"])
        reloaded = SourceStore(self.store.directory)
        self.assertEqual(reloaded.active_id(), record["id"])
        self.assertEqual(reloaded.get(record["id"]), saved)
        self.assertEqual(reloaded.get("sample")["revision"], 1)

    def test_stale_editor_cannot_overwrite_newer_zones(self):
        self.store.save_zones("sample", [ZONE], 1, "balanced")
        response = self.client.post("/api/sources/sample/zones", json={"zones": [], "revision": 1, "smoothing": "balanced"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.store.get("sample")["zones"]), 1)

    def test_bad_upload_leaves_source_and_library_unchanged(self):
        for filename, content in [("bad.mp4", b"not a video"), ("bad.exe", b"content"), ("empty.mp4", b"")]:
            with self.subTest(filename=filename):
                response = self.client.post("/api/upload", data={"video": (io.BytesIO(content), filename)})
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.active_id(), "sample")
        self.assertFalse(list(self.store.directory.glob("upload-*")))

    def test_upload_size_limit_and_missing_file_report_json(self):
        self.app.config["MAX_CONTENT_LENGTH"] = 64
        response = self.client.post("/api/upload", data={"video": (io.BytesIO(b"x" * 1000), "big.mp4")})
        self.assertEqual(response.status_code, 413)
        self.assertIn("error", response.get_json())
        self.app.config["MAX_CONTENT_LENGTH"] = 1024
        self.assertEqual(self.client.post("/api/upload").status_code, 400)

    def test_unknown_video_and_invalid_preset_are_rejected(self):
        self.assertEqual(self.client.post("/api/sources/activate", json={"source_id": "../main.py"}).status_code, 400)
        self.assertEqual(self.client.get("/api/sources/no-such-video/zones").status_code, 400)
        for preset in [None, [], {}, "invalid"]:
            response = self.client.post("/api/sources/sample/zones", json={"zones": [ZONE], "revision": 1, "smoothing": preset})
            self.assertEqual(response.status_code, 400)

    def test_upload_api_and_source_selection(self):
        with DEFAULT_VIDEO.open("rb") as handle:
            response = self.client.post("/api/upload", data={"video": (handle, "lot.mp4")})
        self.assertEqual(response.status_code, 201)
        source_id = response.get_json()["source"]["id"]
        response.request.input_stream.close()
        response = self.client.post("/api/sources/activate", json={"source_id": source_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source_id"], source_id)
        self.assertEqual(self.store.active_id(), source_id)
        self.assertEqual(self.client.get("/api/status").get_json()["source_id"], source_id)

    def test_other_sites_cannot_upload_or_change_zones(self):
        response = self.client.post("/api/sources/activate", json={"source_id": "sample"}, headers={"Origin": "https://elsewhere.example"})
        self.assertEqual(response.status_code, 403)

    def test_unconfigured_video_shows_preview_without_running_model(self):
        record = self.upload_sample()
        stream = ParkingStream(self.store.video_path(record), record)
        try:
            with patch("app.ParkingDetector") as detector:
                stream.start()
                with stream.condition:
                    stream.condition.wait_for(lambda: stream.state["status"] != "loading", timeout=5)
                self.assertEqual(stream.snapshot()["status"], "needs_zones")
                self.assertIsNone(stream.snapshot()["occupied"])
                self.assertIsNotNone(stream.jpeg)
                detector.assert_not_called()
                with self.assertRaises(RuntimeError):
                    stream.control("resume")
        finally:
            stream.stop()
        self.assertFalse(stream.thread.is_alive())


if __name__ == "__main__":
    unittest.main()

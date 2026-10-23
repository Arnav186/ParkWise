"""Local web dashboard with one shared inference worker for all browsers."""

import argparse
import atexit
import copy
import logging
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import cv2
from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from detector import DEFAULT_VIDEO, ParkingDetector
from sources import SourceStore, ConflictError, MAX_UPLOAD_BYTES, PRESETS

LOGGER = logging.getLogger(__name__)


class ParkingStream:
    def __init__(self, source=DEFAULT_VIDEO, record=None, detector=None):
        self.source = Path(source)
        self.record = record
        self.detector = detector
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.thread = None
        self.jpeg = None
        self.sequence = 0
        self.paused = False
        self.restart_requested = False
        self.state = {
            "status": "loading", "error": None, "total": 0,
            "occupied": None, "available": None, "spaces": [], "fps": 0,
            "device": "initializing", "model": "YOLOv8n", "source": self.source.name,
            "position_seconds": 0, "duration_seconds": 0, "frame_index": 0,
            "frame_count": 0, "loops": 0, "confidence": 0.35,
            "pending": 0, "source_id": record["id"] if record else "sample",
            "stream_id": uuid.uuid4().hex,
            "revision": record["revision"] if record else 0,
            "smoothing": record["smoothing"] if record else "balanced",
        }
        if record:
            self.state["source"] = record["name"]
            self.state["total"] = len(record["zones"])
            self.state["pending"] = len(record["zones"])

    def start(self):
        with self.condition:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="parking-inference", daemon=True)
                self.thread.start()

    def snapshot(self):
        with self.condition:
            return copy.deepcopy(self.state)

    def control(self, action):
        with self.condition:
            if self.state["status"] not in ("running", "paused"):
                raise RuntimeError("The detector is not ready yet.")
            if action == "pause":
                self.paused = True
                self.state["status"] = "paused"
            elif action == "resume":
                self.paused = False
                self.state["status"] = "running"
            elif action == "restart":
                self.restart_requested = True
                self.paused = False
                self.state["status"] = "running"
            self.condition.notify_all()
            return copy.deepcopy(self.state)

    def stop(self):
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise RuntimeError("The detector is still stopping. Please retry in a moment.")

    def frames(self):
        last_sequence = -1
        while not self.stop_event.is_set():
            with self.condition:
                self.condition.wait_for(
                    lambda: self.sequence != last_sequence or self.state["status"] == "error"
                    or self.stop_event.is_set(), timeout=1,
                )
                if self.state["status"] == "error" or self.stop_event.is_set():
                    return
                payload, last_sequence = self.jpeg, self.sequence
            if payload is not None:
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload + b"\r\n"

    def _run(self):
        cap = None
        try:
            if not self.source.is_file():
                raise FileNotFoundError(f"Sample video not found: {self.source}")
            cap = cv2.VideoCapture(str(self.source))
            if not cap.isOpened():
                raise RuntimeError("The sample video could not be opened.")
            source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            stride = max(1, round(source_fps / 10))
            if self.record is not None and not self.record["zones"]:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("The video has no readable frames.")
                ok, encoded = cv2.imencode(".jpg", frame)
                if not ok:
                    raise RuntimeError("The preview could not be encoded.")
                with self.condition:
                    self.jpeg = encoded.tobytes()
                    self.sequence += 1
                    self.state.update(status="needs_zones", device="Waiting for zones", frame_count=frame_count,
                                      duration_seconds=frame_count / source_fps)
                    self.condition.notify_all()
                self.stop_event.wait()
                return
            detector = self.detector or ParkingDetector()
            self.detector = detector
            if self.stop_event.is_set():
                return
            if self.record:
                detector.configure(self.record["zones"], self.record["width"], self.record["height"],
                                   *PRESETS[self.record["smoothing"]])
            with self.condition:
                self.state.update(total=len(detector.zones), device=detector.device.upper(),
                                  frame_count=frame_count, duration_seconds=frame_count / source_fps)
            while not self.stop_event.is_set():
                with self.condition:
                    self.condition.wait_for(lambda: not self.paused or self.stop_event.is_set())
                    if self.stop_event.is_set():
                        break
                    if self.restart_requested:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        detector.smoother.reset()
                        self.state["loops"] = 0
                        self.restart_requested = False
                started = time.perf_counter()
                ok, frame = cap.read()
                if not ok:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    detector.smoother.reset()
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("The sample video contains no readable frames.")
                    with self.condition:
                        self.state["loops"] += 1
                frame_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                inference_started = time.perf_counter()
                frame, counts = detector.analyze(frame)
                elapsed = time.perf_counter() - inference_started
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ok:
                    raise RuntimeError("Could not encode the camera frame.")
                with self.condition:
                    # Controls arriving during inference take priority over this frame.
                    if not self.paused and not self.restart_requested:
                        self.jpeg = jpeg.tobytes()
                        self.sequence += 1
                        self.state.update(counts, status="running", fps=round(1 / max(elapsed, 0.001), 1),
                                          frame_index=frame_index, position_seconds=frame_index / source_fps)
                        self.condition.notify_all()
                for _ in range(stride - 1):
                    if not cap.grab():
                        break
                self.stop_event.wait(max(0, stride / source_fps - (time.perf_counter() - started)))
        except Exception as error:
            LOGGER.exception("Parking detector failed")
            with self.condition:
                self.state.update(status="error", error=str(error))
                self.condition.notify_all()
        finally:
            if cap is not None:
                cap.release()


class ParkingWorkspace:
    """Serialize source changes and reuse the model only after its worker stops."""

    def __init__(self, stream=None, store=None):
        self.lock = threading.RLock()
        self.store = store
        self.running = False
        if stream is None:
            self.store = store or SourceStore()
            record = self.store.get(self.store.active_id())
            stream = ParkingStream(self.store.video_path(record), record)
        self.stream = stream

    def start(self):
        with self.lock:
            self.running = True
            self.stream.start()

    def stop(self):
        with self.lock:
            self.running = False
            self.stream.stop()

    def activate(self, source_id):
        with self.lock:
            record = self.store.get(source_id)
            self.stream.stop()
            detector = self.stream.detector
            self.store.set_active(source_id)
            self.stream = ParkingStream(self.store.video_path(record), record, detector)
            if self.running:
                self.stream.start()
            return self.stream.snapshot()


def create_app(stream=None, store=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 1024
    workspace = ParkingWorkspace(stream, store)
    app.extensions["parking_workspace"] = workspace

    @app.before_request
    def same_origin():
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and urlsplit(origin).netloc != request.host:
            return jsonify(error="Use this dashboard to make changes."), 403

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(error):
        return jsonify(error="Videos must be 500 MB or smaller."), 413

    @app.errorhandler(ConflictError)
    def conflict(error):
        return jsonify(error=str(error)), 409

    @app.errorhandler(ValueError)
    def invalid_request(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(RuntimeError)
    def temporarily_unavailable(error):
        return jsonify(error=str(error)), 409

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        with workspace.lock:
            return jsonify(workspace.stream.snapshot())

    @app.post("/api/control")
    def control():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get("action") not in ("pause", "resume", "restart"):
            return jsonify(error="Use action: pause, resume, or restart."), 400
        try:
            with workspace.lock:
                return jsonify(workspace.stream.control(body["action"]))
        except RuntimeError as error:
            return jsonify(error=str(error)), 409

    @app.get("/video_feed")
    def video_feed():
        stream = workspace.stream
        state = stream.snapshot()
        if state["status"] == "error":
            return jsonify(error=state["error"]), 503
        return Response(stream.frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/sources")
    def sources():
        return jsonify(sources=workspace.store.list(), active_id=workspace.stream.state["source_id"])

    @app.post("/api/upload")
    def upload():
        file = request.files.get("video")
        if file is None:
            raise ValueError("Choose a video to upload.")
        record = workspace.store.upload(file)
        return jsonify(source=record), 201

    @app.post("/api/sources/activate")
    def activate():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("Choose a video.")
        return jsonify(workspace.activate(body.get("source_id")))

    @app.get("/api/sources/<source_id>/zones")
    def zones(source_id):
        return jsonify(workspace.store.get(source_id))

    @app.get("/api/sources/<source_id>/preview")
    def preview(source_id):
        workspace.store.get(source_id)
        return send_file(workspace.store.directory / f"{source_id}.jpg", mimetype="image/jpeg")

    @app.post("/api/sources/<source_id>/zones")
    def save_zones(source_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("Supply parking zones and the revision shown in the editor.")
        with workspace.lock:
            record = workspace.store.save_zones(source_id, body.get("zones"), body.get("revision"), body.get("smoothing"))
            if workspace.stream.state["source_id"] == source_id:
                workspace.activate(source_id)
            return jsonify(record)

    return app


def main():
    parser = argparse.ArgumentParser(description="Local smart parking dashboard")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    workspace = app.extensions["parking_workspace"]
    atexit.register(workspace.stop)
    workspace.start()
    from waitress import serve
    print(f"Smart Parking is available at http://localhost:{args.port}", flush=True)
    try:
        serve(app, host="127.0.0.1", port=args.port, threads=8)
    finally:
        workspace.stop()


if __name__ == "__main__":
    main()

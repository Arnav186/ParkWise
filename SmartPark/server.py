"""Vehicle Parking System - dependency-free web server and SQLite API."""

from __future__ import annotations

import json
import mimetypes
import sqlite3
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "parking.db"
WEB_ROOT = ROOT / "web"
DESTINATIONS = ROOT / "destinations.json"


def recommend(db, destination, preference):
    """Explainable heuristic, not a forecast or reservation."""
    candidates = []
    unmapped = full = 0
    for row in db.execute("SELECT * FROM parking_areas"):
        area = area_dict(row)
        if area["available"] == 0:
            full += 1
            continue
        minutes = destination["walkMinutes"].get(area["name"])
        if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes < 0:
            unmapped += 1
            continue
        ratio = area["occupied"] / area["capacity"]
        penalty = 8 if ratio >= 0.9 else 3 if ratio >= 0.75 else 0
        score = minutes + penalty
        if preference == "walk":
            score = minutes
        elif preference == "space":
            score = -area["available"]
        candidates.append({**area, "walkMinutes": minutes, "score": score,
                           "pressure": "Nearly full" if ratio >= 0.9 else "Busy" if ratio >= 0.75 else "Space available"})
    candidates.sort(key=lambda item: (item["score"], item["walkMinutes"], -item["available"], item["id"]))
    reason = None
    if candidates:
        best = candidates[0]
        reason = {"balanced": "Best balance of walking time and current occupancy.",
                  "walk": "Shortest configured walk among areas with available spaces.",
                  "space": "Most available spaces among areas with a configured walking route."}[preference]
        closer = [item for item in candidates[1:] if item["walkMinutes"] < best["walkMinutes"]]
        if preference == "balanced" and closer:
            reason += f" {closer[0]['name']} is closer, but has higher occupancy."
    return {"options": candidates, "reason": reason, "unmappedAreas": unmapped, "fullAreas": full}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connection() -> sqlite3.Connection:
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def setup_database() -> None:
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS parking_areas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                location TEXT NOT NULL DEFAULT '',
                capacity INTEGER NOT NULL CHECK(capacity > 0),
                occupied INTEGER NOT NULL DEFAULT 0 CHECK(occupied >= 0 AND occupied <= capacity),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS occupancy_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                area_id INTEGER NOT NULL REFERENCES parking_areas(id) ON DELETE CASCADE,
                change INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS camera_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                endpoint TEXT NOT NULL DEFAULT '',
                area_id INTEGER REFERENCES parking_areas(id) ON DELETE SET NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scan_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES camera_sources(id) ON DELETE CASCADE,
                area_id INTEGER NOT NULL REFERENCES parking_areas(id) ON DELETE CASCADE,
                detected_occupied INTEGER NOT NULL CHECK(detected_occupied >= 0),
                confidence REAL CHECK(confidence >= 0 AND confidence <= 1),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS parking_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_number TEXT NOT NULL,
                vehicle_type TEXT NOT NULL DEFAULT 'Car',
                area_id INTEGER NOT NULL REFERENCES parking_areas(id) ON DELETE RESTRICT,
                entry_time TEXT NOT NULL,
                exit_time TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS active_vehicle_number
            ON parking_sessions(vehicle_number) WHERE exit_time IS NULL;
            """
        )
        if db.execute("SELECT COUNT(*) FROM parking_areas").fetchone()[0] == 0:
            timestamp = now()
            areas = [
                ("North Gate", "Academic Block", 60, 32, timestamp, timestamp),
                ("Library Lot", "Central Library", 40, 37, timestamp, timestamp),
                ("Sports Complex", "West Campus", 80, 80, timestamp, timestamp),
            ]
            db.executemany(
                "INSERT INTO parking_areas (name, location, capacity, occupied, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                areas,
            )
        if db.execute("SELECT COUNT(*) FROM camera_sources").fetchone()[0] == 0:
            db.execute("INSERT INTO camera_sources (name, endpoint, area_id, enabled, created_at) VALUES (?, ?, ?, ?, ?)", ("North Gate Camera", "rtsp://camera-host/north-gate", 1, 1, now()))


def area_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "location": row["location"],
        "capacity": row["capacity"], "occupied": row["occupied"],
        "available": row["capacity"] - row["occupied"],
        "status": "Full" if row["occupied"] == row["capacity"] else "Available",
        "updatedAt": row["updated_at"],
        "occupancyPercent": round((row["occupied"] / row["capacity"]) * 100),
    }


def as_int(value, field: str, minimum: int | None = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a whole number.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a whole number.") from None
    if minimum is not None and number < minimum:
        raise ValueError(f"{field} must be at least {minimum}.")
    return number


class ParkingHandler(SimpleHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if urlparse(self.path).path.startswith("/api/"):
            return self.send_json({"error": message or HTTPStatus(code).phrase}, code)
        return super().send_error(code, message, explain)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Request body must contain valid JSON.") from None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/api/destinations", "/api/recommendations"):
            try:
                config = json.loads(DESTINATIONS.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return self.send_json({"error": "Destination configuration is unavailable."}, 503)
            if path == "/api/destinations":
                return self.send_json({"demo": config["demo"], "destinations": [{"id": d["id"], "name": d["name"]} for d in config["destinations"]]})
            query = parse_qs(urlparse(self.path).query)
            destination = next((d for d in config["destinations"] if d["id"] == query.get("destination", [""])[0]), None)
            preference = query.get("preference", ["balanced"])[0]
            if destination is None or preference not in ("balanced", "walk", "space"):
                return self.send_json({"error": "Choose a valid destination and preference."}, 400)
            with connection() as db:
                result = recommend(db, destination, preference)
            return self.send_json({**result, "demo": config["demo"], "calculatedAt": now()})
        if path == "/api/areas":
            with connection() as db:
                rows = db.execute("SELECT * FROM parking_areas ORDER BY name").fetchall()
            return self.send_json({"areas": [area_dict(row) for row in rows]})
        if path == "/api/summary":
            with connection() as db:
                row = db.execute("SELECT COUNT(*) areas, COALESCE(SUM(capacity),0) capacity, COALESCE(SUM(occupied),0) occupied FROM parking_areas").fetchone()
                active_sessions = db.execute("SELECT COUNT(*) FROM parking_sessions WHERE exit_time IS NULL").fetchone()[0]
            return self.send_json({"areas": row["areas"], "capacity": row["capacity"], "occupied": row["occupied"], "available": row["capacity"] - row["occupied"], "activeSessions": active_sessions})
        if path == "/api/events":
            with connection() as db:
                rows = db.execute(
                    """SELECT occupancy_events.*, parking_areas.name AS area_name
                       FROM occupancy_events JOIN parking_areas ON parking_areas.id = occupancy_events.area_id
                       ORDER BY occupancy_events.id DESC LIMIT 12"""
                ).fetchall()
            return self.send_json({"events": [{"id": row["id"], "areaId": row["area_id"], "areaName": row["area_name"], "change": row["change"], "reason": row["reason"], "createdAt": row["created_at"]} for row in rows]})
        if path == "/api/camera-sources":
            with connection() as db:
                rows = db.execute("""SELECT camera_sources.*, parking_areas.name AS area_name
                    FROM camera_sources LEFT JOIN parking_areas ON parking_areas.id = camera_sources.area_id
                    ORDER BY camera_sources.name""").fetchall()
            return self.send_json({"sources": [{"id": row["id"], "name": row["name"], "endpoint": row["endpoint"], "areaId": row["area_id"], "areaName": row["area_name"], "enabled": bool(row["enabled"])} for row in rows]})
        if path == "/api/scan-events":
            with connection() as db:
                rows = db.execute("""SELECT scan_events.*, camera_sources.name AS source_name, parking_areas.name AS area_name
                    FROM scan_events JOIN camera_sources ON camera_sources.id = scan_events.source_id
                    JOIN parking_areas ON parking_areas.id = scan_events.area_id ORDER BY scan_events.id DESC LIMIT 10""").fetchall()
            return self.send_json({"scans": [{"id": row["id"], "sourceName": row["source_name"], "areaName": row["area_name"], "detectedOccupied": row["detected_occupied"], "confidence": row["confidence"], "createdAt": row["created_at"]} for row in rows]})
        if path == "/api/sessions":
            with connection() as db:
                rows = db.execute("""SELECT parking_sessions.*, parking_areas.name AS area_name
                    FROM parking_sessions JOIN parking_areas ON parking_areas.id = parking_sessions.area_id
                    WHERE parking_sessions.exit_time IS NULL ORDER BY parking_sessions.entry_time DESC LIMIT 20""").fetchall()
            return self.send_json({"sessions": [{"id": row["id"], "vehicleNumber": row["vehicle_number"], "vehicleType": row["vehicle_type"], "areaName": row["area_name"], "entryTime": row["entry_time"]} for row in rows]})
        if path.startswith("/api/"):
            return self.send_json({"error": "API route not found. Restart python server.py if you just updated the project."}, HTTPStatus.NOT_FOUND)
        return self.serve_file(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/areas":
                name = str(payload.get("name", "")).strip()
                location = str(payload.get("location", "")).strip()
                capacity = as_int(payload.get("capacity"), "Capacity", 1)
                if not name:
                    raise ValueError("Area name is required.")
                timestamp = now()
                with connection() as db:
                    cursor = db.execute("INSERT INTO parking_areas (name, location, capacity, occupied, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)", (name, location, capacity, timestamp, timestamp))
                    row = db.execute("SELECT * FROM parking_areas WHERE id = ?", (cursor.lastrowid,)).fetchone()
                return self.send_json({"area": area_dict(row)}, HTTPStatus.CREATED)
            if path == "/api/camera-sources":
                name = str(payload.get("name", "")).strip()
                endpoint = str(payload.get("endpoint", "")).strip()
                area_id = as_int(payload.get("areaId"), "Parking area", 1)
                if not name:
                    raise ValueError("Camera name is required.")
                with connection() as db:
                    if db.execute("SELECT 1 FROM parking_areas WHERE id = ?", (area_id,)).fetchone() is None:
                        raise ValueError("Choose an existing parking area.")
                    cursor = db.execute("INSERT INTO camera_sources (name, endpoint, area_id, created_at) VALUES (?, ?, ?, ?)", (name, endpoint, area_id, now()))
                    source = db.execute("SELECT * FROM camera_sources WHERE id = ?", (cursor.lastrowid,)).fetchone()
                return self.send_json({"source": {"id": source["id"], "name": source["name"]}}, HTTPStatus.CREATED)
            if path == "/api/scan-events":
                source_id = as_int(payload.get("sourceId"), "Camera source", 1)
                detected = as_int(payload.get("detectedOccupied"), "Detected occupancy")
                try:
                    confidence = float(payload.get("confidence", 0.9))
                except (TypeError, ValueError):
                    raise ValueError("Confidence must be a number between 0 and 1.") from None
                if confidence < 0 or confidence > 1:
                    raise ValueError("Confidence must be between 0 and 1.")
                with connection() as db:
                    source = db.execute("SELECT * FROM camera_sources WHERE id = ? AND enabled = 1", (source_id,)).fetchone()
                    if source is None:
                        raise ValueError("Choose an enabled camera source.")
                    area = db.execute("SELECT * FROM parking_areas WHERE id = ?", (source["area_id"],)).fetchone()
                    if detected > area["capacity"]:
                        raise ValueError(f"Detected occupancy cannot exceed the capacity of {area['capacity']}.")
                    timestamp = now()
                    db.execute("INSERT INTO scan_events (source_id, area_id, detected_occupied, confidence, created_at) VALUES (?, ?, ?, ?, ?)", (source_id, area["id"], detected, confidence, timestamp))
                    delta = detected - area["occupied"]
                    db.execute("UPDATE parking_areas SET occupied = ?, updated_at = ? WHERE id = ?", (detected, timestamp, area["id"]))
                    db.execute("INSERT INTO occupancy_events (area_id, change, reason, created_at) VALUES (?, ?, ?, ?)", (area["id"], delta, f"Camera scan: {source['name']}", timestamp))
                return self.send_json({"area": {"id": area["id"], "name": area["name"], "occupied": detected}, "change": delta})
            if path == "/api/sessions":
                vehicle_number = str(payload.get("vehicleNumber", "")).strip().upper()
                vehicle_type = str(payload.get("vehicleType", "Car")).strip() or "Car"
                area_id = as_int(payload.get("areaId"), "Parking area", 1)
                if not 3 <= len(vehicle_number) <= 20 or not all(char.isalnum() or char in " -" for char in vehicle_number):
                    raise ValueError("Enter a valid vehicle number using letters, numbers, spaces, or hyphens.")
                with connection() as db:
                    area = db.execute("SELECT * FROM parking_areas WHERE id = ?", (area_id,)).fetchone()
                    if area is None:
                        raise ValueError("Choose an existing parking area.")
                    if area["occupied"] >= area["capacity"]:
                        raise ValueError(f"{area['name']} is full.")
                    if db.execute("SELECT 1 FROM parking_sessions WHERE vehicle_number = ? AND exit_time IS NULL", (vehicle_number,)).fetchone():
                        raise ValueError("This vehicle already has an active parking session.")
                    timestamp = now()
                    cursor = db.execute("INSERT INTO parking_sessions (vehicle_number, vehicle_type, area_id, entry_time) VALUES (?, ?, ?, ?)", (vehicle_number, vehicle_type, area_id, timestamp))
                    db.execute("UPDATE parking_areas SET occupied = occupied + 1, updated_at = ? WHERE id = ?", (timestamp, area_id))
                    db.execute("INSERT INTO occupancy_events (area_id, change, reason, created_at) VALUES (?, ?, ?, ?)", (area_id, 1, f"Vehicle check-in: {vehicle_number}", timestamp))
                return self.send_json({"session": {"id": cursor.lastrowid, "vehicleNumber": vehicle_number}}, HTTPStatus.CREATED)
            if path.startswith("/api/sessions/") and path.endswith("/checkout"):
                session_id = as_int(path.split("/")[3], "Session id", 1)
                with connection() as db:
                    session = db.execute("SELECT * FROM parking_sessions WHERE id = ? AND exit_time IS NULL", (session_id,)).fetchone()
                    if session is None:
                        return self.send_json({"error": "Active parking session not found."}, HTTPStatus.NOT_FOUND)
                    timestamp = now()
                    db.execute("UPDATE parking_sessions SET exit_time = ? WHERE id = ?", (timestamp, session_id))
                    db.execute("UPDATE parking_areas SET occupied = occupied - 1, updated_at = ? WHERE id = ?", (timestamp, session["area_id"]))
                    db.execute("INSERT INTO occupancy_events (area_id, change, reason, created_at) VALUES (?, ?, ?, ?)", (session["area_id"], -1, f"Vehicle check-out: {session['vehicle_number']}", timestamp))
                return self.send_json({"checkedOut": session["vehicle_number"]})
            if path.startswith("/api/areas/") and path.endswith("/occupancy"):
                area_id = as_int(path.split("/")[3], "Area id", 1)
                change = as_int(payload.get("change"), "Change", None) if payload.get("change") is not None else None
                occupied = as_int(payload.get("occupied"), "Occupied vehicles") if payload.get("occupied") is not None else None
                if (change is None) == (occupied is None):
                    raise ValueError("Provide either a change or an occupied value.")
                reason = str(payload.get("reason", "Manual update")).strip() or "Manual update"
                with connection() as db:
                    row = db.execute("SELECT * FROM parking_areas WHERE id = ?", (area_id,)).fetchone()
                    if row is None:
                        return self.send_json({"error": "Parking area not found."}, HTTPStatus.NOT_FOUND)
                    next_occupied = occupied if occupied is not None else row["occupied"] + change
                    if next_occupied < 0 or next_occupied > row["capacity"]:
                        raise ValueError(f"Occupancy must stay between 0 and {row['capacity']}.")
                    delta = next_occupied - row["occupied"]
                    timestamp = now()
                    db.execute("UPDATE parking_areas SET occupied = ?, updated_at = ? WHERE id = ?", (next_occupied, timestamp, area_id))
                    db.execute("INSERT INTO occupancy_events (area_id, change, reason, created_at) VALUES (?, ?, ?, ?)", (area_id, delta, reason, timestamp))
                    row = db.execute("SELECT * FROM parking_areas WHERE id = ?", (area_id,)).fetchone()
                return self.send_json({"area": area_dict(row)})
            return self.send_json({"error": "Route not found."}, HTTPStatus.NOT_FOUND)
        except sqlite3.IntegrityError:
            return self.send_json({"error": "An area with that name already exists."}, HTTPStatus.CONFLICT)
        except ValueError as error:
            return self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/areas/"):
            return self.send_json({"error": "Route not found."}, HTTPStatus.NOT_FOUND)
        try:
            area_id = as_int(path.split("/")[3], "Area id", 1)
            payload = self.read_json()
            with connection() as db:
                current = db.execute("SELECT * FROM parking_areas WHERE id = ?", (area_id,)).fetchone()
                if current is None:
                    return self.send_json({"error": "Parking area not found."}, HTTPStatus.NOT_FOUND)
                name = str(payload.get("name", current["name"])).strip()
                location = str(payload.get("location", current["location"])).strip()
                capacity = as_int(payload.get("capacity", current["capacity"]), "Capacity", 1)
                if not name:
                    raise ValueError("Area name is required.")
                if capacity < current["occupied"]:
                    raise ValueError(f"Capacity cannot be lower than the current occupancy of {current['occupied']}.")
                db.execute("UPDATE parking_areas SET name = ?, location = ?, capacity = ?, updated_at = ? WHERE id = ?", (name, location, capacity, now(), area_id))
                row = db.execute("SELECT * FROM parking_areas WHERE id = ?", (area_id,)).fetchone()
            return self.send_json({"area": area_dict(row)})
        except sqlite3.IntegrityError:
            return self.send_json({"error": "An area with that name already exists."}, HTTPStatus.CONFLICT)
        except (IndexError, ValueError) as error:
            return self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/areas/"):
            return self.send_json({"error": "Route not found."}, HTTPStatus.NOT_FOUND)
        try:
            area_id = as_int(path.split("/")[3], "Area id", 1)
        except (IndexError, ValueError) as error:
            return self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        with connection() as db:
            if db.execute("DELETE FROM parking_areas WHERE id = ?", (area_id,)).rowcount == 0:
                return self.send_json({"error": "Parking area not found."}, HTTPStatus.NOT_FOUND)
        return self.send_json({}, HTTPStatus.NO_CONTENT)

    def serve_file(self, request_path: str) -> None:
        relative = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT not in target.parents and target != WEB_ROOT:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    setup_database()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), ParkingHandler)
    print("Vehicle Parking System running at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()

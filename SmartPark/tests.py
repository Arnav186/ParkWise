"""Core checks for the Vehicle Parking System data layer and validation rules."""
import sqlite3
from pathlib import Path
from uuid import uuid4

import server

test_database = Path(__file__).resolve().parent / "work" / f"test-parking-{uuid4().hex}.db"

try:
    server.DATABASE = test_database
    server.setup_database()
    with server.connection() as db:
        seeded = db.execute("SELECT COUNT(*) FROM parking_areas").fetchone()[0]
        assert seeded == 3
        assert db.execute("SELECT COUNT(*) FROM camera_sources").fetchone()[0] == 1
        timestamp = server.now()
        db.execute(
            "INSERT INTO parking_areas (name, location, capacity, occupied, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("Test Area", "Lab", 2, 0, timestamp, timestamp),
        )
        test_area = db.execute("SELECT * FROM parking_areas WHERE name = 'Test Area'").fetchone()
        assert server.area_dict(test_area)["available"] == 2
        assert server.area_dict(test_area)["occupancyPercent"] == 0
        db.execute("UPDATE parking_areas SET occupied = 1 WHERE id = ?", (test_area["id"],))
        assert db.execute("SELECT occupied FROM parking_areas WHERE id = ?", (test_area["id"],)).fetchone()[0] == 1
        db.execute("INSERT INTO occupancy_events (area_id, change, reason, created_at) VALUES (?, ?, ?, ?)", (test_area["id"], 1, "Vehicle entry", timestamp))
        assert db.execute("SELECT COUNT(*) FROM occupancy_events WHERE area_id = ?", (test_area["id"],)).fetchone()[0] == 1
        camera_id = db.execute("INSERT INTO camera_sources (name, endpoint, area_id, created_at) VALUES (?, ?, ?, ?)", ("Lab camera", "", test_area["id"], timestamp)).lastrowid
        db.execute("INSERT INTO scan_events (source_id, area_id, detected_occupied, confidence, created_at) VALUES (?, ?, ?, ?, ?)", (camera_id, test_area["id"], 1, 0.92, timestamp))
        assert db.execute("SELECT detected_occupied FROM scan_events WHERE source_id = ?", (camera_id,)).fetchone()[0] == 1
        session_id = db.execute("INSERT INTO parking_sessions (vehicle_number, vehicle_type, area_id, entry_time) VALUES (?, ?, ?, ?)", ("MH 31 AB 1234", "Car", test_area["id"], timestamp)).lastrowid
        assert db.execute("SELECT COUNT(*) FROM parking_sessions WHERE exit_time IS NULL").fetchone()[0] == 1
        try:
            db.execute("INSERT INTO parking_sessions (vehicle_number, vehicle_type, area_id, entry_time) VALUES (?, ?, ?, ?)", ("MH 31 AB 1234", "Car", test_area["id"], timestamp))
            raise AssertionError("Duplicate active vehicle session was accepted")
        except sqlite3.IntegrityError:
            pass
        db.execute("UPDATE parking_sessions SET exit_time = ? WHERE id = ?", (timestamp, session_id))
        try:
            db.execute("UPDATE parking_areas SET occupied = 3 WHERE id = ?", (test_area["id"],))
            raise AssertionError("Database accepted occupancy above capacity")
        except sqlite3.IntegrityError:
            pass
    assert server.as_int("-1", "Change", None) == -1
    try:
        server.as_int("-1", "Capacity", 1)
        raise AssertionError("Negative capacity was accepted")
    except ValueError:
        pass
    print("All core checks passed.")
finally:
    pass

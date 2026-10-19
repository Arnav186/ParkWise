# Vehicle Parking System

A software-first parking management web app based on the supplied synopsis. It supports multiple parking areas, capacity configuration, live availability, searchable and filtered area cards, occupancy activity history, manual count adjustments, editable area details, persistent SQLite storage, and validation that prevents impossible occupancy values.

## Camera-ready design

The Camera Integration section lets an administrator assign a camera source to a parking area and simulate its occupancy output. A future detector can submit a JSON request to `POST /api/scan-events`:

```json
{
  "sourceId": 1,
  "detectedOccupied": 35,
  "confidence": 0.92
}
```

The server validates the reading against the linked area's capacity, stores the scan history, updates the live count, and logs the resulting occupancy change. The current project does not open camera streams or identify vehicle plates.

## Vehicle sessions

The dashboard also includes manual check-in and checkout. A vehicle number can have only one active parking session at a time. Check-in validates the selected area's capacity and increases its occupancy; checkout closes the session and reduces occupancy. This same workflow can be fed later by license-plate recognition, while keeping the camera detector and vehicle-session concerns separate.

## Run locally

## Find My Best Spot

Choose a destination and a priority to compare available parking areas.
Balanced ranking adds an occupancy penalty to walking minutes: 3 minutes at
75–89% occupancy, 8 minutes at 90% or more. Shortest walk sorts by walking
time; most spaces sorts by free capacity. Full areas are always excluded.
This is a transparent rule, not a machine-learning prediction or reservation.

Edit `destinations.json` to replace demo destinations and walking times with
your campus measurements. `walkMinutes` keys must match parking-area names
exactly, including after renaming an area. Add routes for new parking areas;
unmapped areas are explicitly excluded. Set `demo` to false only after replacing
the sample walking times. Changes to this file are read on each request.
Recommendations show the recorded occupancy timestamp and refresh after app
updates or when you click Find my spot. No camera confidence or forecasting
claims are made by this feature.

### Starting the app

From this folder, run:

```powershell
python server.py
```

Open `http://127.0.0.1:8000` in a browser.

After updating the source code, stop the running server with Ctrl+C and run
`python server.py` again. Python does not automatically reload this server.
Use that server for both the page and the API. VS Code Live Server, a static
preview, `python -m http.server`, and opening `web/index.html` directly cannot
run the backend. They may return an HTML page for `/api/...`, causing the
"Unexpected token '<' ... not valid JSON" error in older versions of the UI.

The app creates `parking.db` automatically and includes three demonstration parking areas the first time it runs.

## Test

```powershell
python tests.py
```

## Project structure

- `server.py` - HTTP server, REST API, SQLite schema, and validation
- `web/` - responsive availability dashboard, activity log, filters, and administrator controls
- `tests.py` - integration tests for core API behavior

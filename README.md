<img width="393" height="736" alt="dashboard" src="https://github.com/user-attachments/assets/e8c63570-4a5d-4ea5-8080-994930fc9560" />

<img width="393" height="736" alt="zone-editor" src="https://github.com/user-attachments/assets/6cad01a4-3474-4f17-bccf-821f9d218b10" />

# Smart Parking Space Detector

A YOLOv8 + OpenCV parking detector with a localhost dashboard and an optional desktop viewer. The bundled recorded video has 18 mapped parking spaces. Green zones are available; coral zones are occupied by detected vehicles.

## Run locally (Windows PowerShell)

Use Python 3.13, the version tested for this project. Run these commands from the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open **http://localhost:5000**. The first frame can take several seconds while YOLO loads. The server listens only on your computer (127.0.0.1). Stop it with Ctrl+C in the terminal where it is running.

After completing setup, launch it again with:

```powershell
.\.venv\Scripts\python.exe app.py
```

For a different port, use `app.py --port 5001`. You can also run `./start.ps1 -Port 5001` if PowerShell script execution is enabled. `requirements-lock.txt` records all package versions used for verification.

## Dashboard

- Annotated video and live occupied/available counts.
- Individual status for each mapped space.
- Pause/resume and restart controls, shared across browser tabs.
- Automatic looping of the 48.68-second sample video.
- Model, processing device, confidence threshold, and inference speed.
- Explicit loading and error states if required assets cannot be read.
- Local video uploads, a saved video library, and separate parking zones for each video.
- Browser zone drawing, moving/resizing, numeric corner editing, delete, and undo.
- Consecutive-frame confirmation to reduce flickering and tolerate brief missed detections.

This is a recorded sample, not a live camera. Inference runs locally on CPU, or CUDA when a compatible PyTorch installation and GPU are available. The browser stream targets approximately 10–12 frames per second; slower computers may play more slowly. The displayed inference FPS measures model processing speed, not browser playback speed.

## Desktop viewer

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe main.py --headless --max-frames 3
.\.venv\Scripts\python.exe main.py --source 0
```

Press Q to close the OpenCV viewer. Camera or replacement video sources need parking zones calibrated for that camera view; the bundled zones belong to the sample video. A different resolution of the same view is scaled automatically.

## Upload a video and edit its zones

1. Choose **Upload video**. MP4, MOV, AVI, MKV, and WebM files up to 500 MB are supported if OpenCV can decode them. Maximum width/height is 4096 pixels.
2. The zone editor opens on the video's first frame. New videos start with no zones and do not report occupancy until configured.
3. Choose **Draw a space** and click four corners in clockwise or counterclockwise order. Alternatively, use **Add rectangle**. Select a space to move it, drag a corner to resize it, or enter precise corner coordinates as percentages. **Delete selected** removes a space; **Undo** restores recent edits.
4. Choose a detection stability preset and select **Save zones & start**. Saving restarts the selected video's detection. Closing the editor discards unsaved changes.
5. Switch saved videos with **Your videos**. Re-uploading identical file contents reuses that video's saved zones.

Video files, previews, zones, stability settings, and the last selected video persist in the ignored `data/` directory. Nothing is uploaded to a cloud service. Each editor save checks a revision to avoid overwriting changes made in another browser tab. Invalid uploads leave the current video unchanged. The original sample's `Space_ROIs` file is preserved.

## Stable detection

The initial state of each space is **checking** (amber) until there is enough consistent evidence. A short detection dropout does not immediately free an occupied space, and a single false positive does not occupy an available one.

| Preset | Occupied / initial confirmation | Available after departure |
| --- | --- | --- |
| Responsive | 2 consecutive processed frames | 4 consecutive processed frames |
| Balanced (default) | 3 consecutive processed frames | 8 consecutive processed frames |
| Steady | 5 consecutive processed frames | 12 consecutive processed frames |

These are processed-frame counts, so confirmation time depends on processing speed. The dashboard processes approximately 10–12 frames per second on a sufficiently fast machine. Stability state resets when restarting, looping, switching videos, or saving zones. Available + occupied + checking always equals the configured space count.

## Desktop parking-zone editor

```powershell
.\.venv\Scripts\python.exe ParkingSpaceSelection.py
```

Four left clicks add a quadrilateral. Right-click inside a space to remove it. Changes save to `Space_ROIs` for the desktop viewer (`main.py`). Press Q to close the editor and restart the desktop viewer to load the new zones. Keep `ROI_Reference.png` aligned with your video when configuring another view. Browser configurations are saved separately in `data/`; they import `Space_ROIs` only when first creating the sample configuration.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Local Flask/Waitress server and shared video worker |
| `detector.py` | Vehicle detection, zone occupancy, and annotation |
| `sources.py` | Validated uploads and persistent per-video configurations |
| `data/` | Local uploaded videos, previews, zones, and selected source (ignored by Git) |
| `templates/`, `static/` | Browser dashboard |
| `main.py` | Optional OpenCV viewer and headless smoke check |
| `ParkingSpaceSelection.py` | Desktop parking-zone editor |
| `Space_ROIs` | Saved coordinates for the 18 sample spaces |
| `ROI_Reference.png` | Reference view used for mapping and scaling |
| `yolov8n.pt` | Bundled YOLOv8 model |
| `input_video/parking_space.mp4` | Bundled sample video, ready to use |
| `docs/Smart_Parking_Idea_Lab_Report.docx` | Editable project report |

The required YOLOv8 model and sample video are included in this repository.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --headless --max-frames 3
.\.venv\Scripts\python.exe -m pip check
```

Occupancy is an estimate: a space is occupied when the center of a detected car, motorcycle, bus, or truck falls inside its polygon. People are excluded. Perspective, occlusion, lighting, and zone mapping can affect detection accuracy.

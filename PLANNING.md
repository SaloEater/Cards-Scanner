# Card Scanner — Application Planning

## Purpose

A Python desktop GUI application for photographing trading cards before they are packed into mystery boxes. The operator scans each card, the app crops and stages the image, and at the end uploads the batch asynchronously to the backend System (WhatNot-Webhook-Holder). The uploaded photos then appear on the Cards Board Page (OBS display) during the livestream.

Spec source: `No-Mod-Livestream/DIGITALIZING_PHOTO_1.md` and `SERIES_AND_IMAGE_FLOW.md`.

---

## Technology Stack

| Concern | Choice | Reason |
|---|---|---|
| GUI framework | **PySide6** (Qt 6) | Rich widget set, native QCamera integration, good OpenCV interop |
| Camera / CV | **OpenCV** (`cv2`) | Card-bounds detection, live preview frame processing, crop |
| HTTP client | **httpx** | Async uploads without blocking the GUI thread |
| Local state | **JSON file** alongside images | Simple, human-readable, crash-safe |
| Python version | 3.11+ | `tomllib`, `pathlib`, match-statement |

---

## Application Screens & State Machine

```
Launch
  ├─ incomplete series detected → Resume Prompt
  │     ├─ Resume → Thumbnail Grid (pre-filled)
  │     └─ Discard → Create Series
  └─ no incomplete series → Create Series
        └─ [Create Series] → Scanning
              └─ [Capture] → Assign team
                    └─ [Assign team] → Review
                          ├─ [Approve] → Scanning (loop)
                          └─ [Retake]  → Scanning (loop)
              └─ [Done scanning] → Thumbnail Grid
                    └─ [Send] → Upload Progress (background)
                          └─ complete → Done / Exit
```

### Screen 1 — Launch / Resume Prompt

- On startup, scan `~/.card-scanner/state-*.json` for files with `status != "uploaded"`.
- If any found: show list of incomplete series (most recent first) — each with name, card count, and status badge. Operator picks one to **Resume** or dismisses to **Start fresh**.
- **"Series History"** button always visible — opens a read-only list of all state files (including uploaded ones) showing series name, card count, and upload date. Useful for reviewing what was in a submitted break.
- If no incomplete series: go directly to Create Series screen.

### Screen 2 — Create Series

- Single text field: series name.
- **"Create Series"** button — calls `POST /api/series` on the backend, stores returned series ID locally, transitions to Scanning.

### Screen 3 — Scanning (main loop)

- **Left panel:** live camera preview at full frame rate (~30 fps) via OpenCV `VideoCapture`.
  - Detected card bounding box drawn as an overlay rectangle.
  - Status label: **"Ready"** (card detected, bounds look clean) or **"Adjust"** (card not found or bounds uncertain).
- **Right panel (collapsed):** thumbnail strip of approved cards so far (count shown).
- **Capture button** (or keyboard shortcut `Space` / USB button mapped to a key):
  - Freezes the frame, crops to card bounds, transitions to Review.
- **"Done Scanning"** button: transitions to Thumbnail Grid.

Card detection algorithm (OpenCV):
1. Convert frame to grayscale → Gaussian blur → Canny edge detection.
2. Find contours → pick the largest quadrilateral that fits the expected card aspect ratio (roughly 2.5:3.5 inches).
3. Perspective-correct the crop so the card fills the output image.

Card text extraction (planned, **not implemented yet**):
- After cropping, run OCR (e.g. `pytesseract` / `easyocr`) on the card image to extract any printed text.
- The extracted text would be used to pre-fill the card name field on the Review screen.
- **Current behavior:** name is always left empty — OCR step is skipped entirely until this feature is built.

### Screen 4 — Review

- Shows the cropped card image at large size.
- **Name field:** editable text input pre-filled by OCR if available, otherwise blank. The operator may type a name or leave it empty — both are valid.
  > **Not implemented yet:** OCR is not running, so the field is always blank. The field is present in the UI so the operator can type a name manually if they choose.
- **"Approve"** button: writes cropped image to `~/.card-scanner/<series-id>/<index>.jpg`, appends entry to `state-<series-id>.json` (including `name`, even if empty), goes to Assign Team screen.
- **"Retake"** button: discards the frame, returns to Scanning without writing anything.

### Screen 4.1 - Assign team

- Shows the photo
- **Team** dropdown from the predefined values
- Selecting a team writes that info into `state-<series-id>.json` and returns to Scanning

### Screen 5 — Thumbnail Grid

- Grid of all approved card thumbnails.
- Series name field (editable — calls `PATCH /api/series/<id>` on Send if changed).
- Card count label.
- **"Send"** button: starts background upload, transitions to Upload Progress.

### Screen 6 — Upload Progress

- Progress bar (N of M photos uploaded).
- Each photo uploaded via `POST /api/series/<id>/photos` with the image file.
- Successfully uploaded photos are marked in `state.json` so retries skip them.
- On completion: show **"Upload complete"** — series is closed on the backend (`POST /api/series/<id>/close`), `status` in `state-<id>.json` set to `"uploaded"`. The file and images are kept on disk for history.
- On error: show per-photo error with **"Retry"** option.

---

## Local State Format

Each series gets its own state file: `~/.card-scanner/state-<series-id>.json`. Files are never deleted after upload — they serve as a local history of all submitted series.

`~/.card-scanner/state-abc123.json`:

```json
{
  "series_id": "abc123",
  "series_name": "March Prizm Lot",
  "status": "uploaded",
  "photos": [
    {
      "index": 0,
      "filename": "0.jpg",
      "name": "",
      "uploaded": true
    }
  ]
}
```

`status` is one of `"scanning"` (in progress) | `"ready"` (all photos staged, not yet sent) | `"uploading"` (send in progress) | `"uploaded"` (closed on backend).

Images stored at `~/.card-scanner/<series-id>/0.jpg`, `1.jpg`, …

On launch the app scans all `state-*.json` files:
- Files with `status != "uploaded"` → offered as Resume candidates (most recent first).
- All files (including uploaded) → available in the Series History view.

---

## Project Structure

```
Card-Scanner/
├── main.py                  # entry point — starts QApplication
├── requirements.txt
├── PLANNING.md
├── app/
│   ├── __init__.py
│   ├── config.py            # backend URL, storage path (env / config file)
│   ├── state.py             # read/write state-<id>.json files, crash-safe (write-then-rename)
│   ├── models.py            # dataclasses: Series, Photo
│   ├── backend.py           # httpx calls: create_series, upload_photo, close_series
│   ├── camera.py            # OpenCV capture thread → emits QPixmap signals
│   ├── detector.py          # card bounds detection (OpenCV pipeline)
│   └── screens/
│       ├── launch.py        # Resume prompt or redirect to create
│       ├── create_series.py
│       ├── scanning.py      # live preview + capture
│       ├── review.py        # approve / retake
│       ├── thumbnail_grid.py
│       └── upload_progress.py
```

---

## Camera Thread Design

OpenCV `VideoCapture` runs in a `QThread` to avoid blocking the GUI. Each frame is:
1. Read by the camera thread.
2. Passed through the detector (bounding box calculated).
3. Converted to `QImage` → `QPixmap`.
4. Emitted as a Qt signal → received by the Scanning screen and displayed in a `QLabel`.

Capture is triggered by a signal from the GUI thread to the camera thread; the camera thread freezes the current frame and emits it back for the Review screen.

---

## Backend API Endpoints Required

These endpoints must exist in WhatNot-Webhook-Holder before the app can upload:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/series` | Create a new series; returns `{ id, name }` |
| `PATCH` | `/api/series/:id` | Rename a series |
| `POST` | `/api/series/:id/photos` | Upload one photo (multipart `file` + optional `name` string); returns `{ photo_id }` |
| `POST` | `/api/series/:id/close` | Mark series as closed (photos become visible on board) |

---

## Open Questions

- What is the backend host URL during local development? (Should match `db_dsn` env convention in WhatNot-Webhook-Holder — probably configurable via `.env` in the app too.)
- Should the app support multiple cameras (e.g., USB webcam vs. phone)? Default to camera index 0 for now.
- What resolution should captured images be saved at? Suggest 1500×2100 px (standard card at ~600 dpi equivalent) as a starting point.
- Should the local state folder be configurable or always `~/.card-scanner`?

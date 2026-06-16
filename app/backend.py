from __future__ import annotations

from pathlib import Path

import httpx

from app import config


def _post(path: str, body: dict) -> dict:
    with httpx.Client(base_url=config.BACKEND_URL, timeout=15) as c:
        r = c.post(path, json=body)
        r.raise_for_status()
        payload = r.json()
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload.get("data") or {}


def create_series(name: str) -> dict:
    return _post("/api/series/create", {"name": name})


def upload_photo(series_id: str, filepath: Path, name: str, team: str = "") -> dict:
    with httpx.Client(base_url=config.BACKEND_URL, timeout=60) as c:
        with open(filepath, "rb") as f:
            r = c.post(
                "/api/photo/upload",
                data={"series_id": series_id, "name": name, "team": team},
                files={"file": (filepath.name, f, "image/jpeg")},
            )
        r.raise_for_status()
        payload = r.json()
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload.get("data") or {}


def close_series(series_id: str) -> None:
    _post("/api/series/close", {"series_id": int(series_id)})

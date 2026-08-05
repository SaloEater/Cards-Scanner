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


def create_series(name: str, total_cards: int = 0, default_price: str = "") -> dict:
    return _post("/api/series/create", {"name": name, "total_cards": total_cards, "default_price": default_price})


def upload_photo(
    series_id: str,
    filepath: Path,
    name: str,
    team: str = "",
    price: str = "",
    rotation: int = 0,
    thumb_path: Path | None = None,
) -> dict:
    with httpx.Client(base_url=config.BACKEND_URL, timeout=60) as c:
        with open(filepath, "rb") as f:
            files = {"file": (filepath.name, f, "image/jpeg")}
            thumb_file = None
            try:
                if thumb_path is not None and thumb_path.exists():
                    thumb_file = open(thumb_path, "rb")
                    files["thumbnail"] = (thumb_path.name, thumb_file, "image/jpeg")
                r = c.post(
                    "/api/photo/upload",
                    data={
                        "series_id": series_id,
                        "name": name,
                        "team": team,
                        "price": price,
                        "rotation": str(rotation),
                    },
                    files=files,
                )
            finally:
                if thumb_file is not None:
                    thumb_file.close()
        r.raise_for_status()
        payload = r.json()
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload.get("data") or {}


def close_series(series_id: str) -> None:
    _post("/api/series/close", {"series_id": int(series_id)})

from __future__ import annotations

import json
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
    art_bounds: tuple[int, int, int, int] | None = None,
    label_bounds: tuple[int, int, int, int] | None = None,
    label_text: dict | None = None,
) -> dict:
    form = {
        "series_id": series_id,
        "name": name,
        "team": team,
        "price": price,
        "rotation": str(rotation),
    }
    for key, b in (("art_bounds", art_bounds), ("label_bounds", label_bounds)):
        if b is not None:
            x, y, w, h = b
            form[key] = json.dumps({"x": x, "y": y, "w": w, "h": h}, separators=(",", ":"))
    if label_text is not None:
        form["label_text"] = json.dumps(label_text, separators=(",", ":"), ensure_ascii=False)
    with httpx.Client(base_url=config.BACKEND_URL, timeout=60) as c:
        with open(filepath, "rb") as f:
            r = c.post(
                "/api/photo/upload",
                data=form,
                files={"file": (filepath.name, f, "image/jpeg")},
            )
        r.raise_for_status()
        payload = r.json()
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload.get("data") or {}


def close_series(series_id: str) -> None:
    _post("/api/series/close", {"series_id": int(series_id)})

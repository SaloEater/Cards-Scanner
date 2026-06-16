from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from app import config
from app.models import Series


def _state_path(series_id: str) -> Path:
    return config.DATA_DIR / f"state-{series_id}.json"


def _tmp_path(series_id: str) -> Path:
    return config.DATA_DIR / f"state-{series_id}.tmp"


def create_series(name: str, series_id: str | None = None) -> Series:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    sid = series_id if series_id is not None else uuid4().hex[:8]
    series = Series(series_id=sid, series_name=name, status="scanning")
    save_series(series)
    return series


def save_series(series: Series) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(series.series_id)
    final = _state_path(series.series_id)
    tmp.write_text(json.dumps(series.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, final)


def load_series(series_id: str) -> Series:
    data = json.loads(_state_path(series_id).read_text(encoding="utf-8"))
    return Series.from_dict(data)


def list_series() -> list[Series]:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    result: list[tuple[float, Series]] = []
    for path in config.DATA_DIR.glob("state-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append((path.stat().st_mtime, Series.from_dict(data)))
        except Exception:
            continue
    result.sort(key=lambda t: t[0], reverse=True)
    return [s for _, s in result]


def ensure_series_dir(series_id: str) -> Path:
    d = config.DATA_DIR / series_id
    d.mkdir(parents=True, exist_ok=True)
    return d

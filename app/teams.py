from __future__ import annotations

import json
from pathlib import Path

from app import config

_DEFAULT_JSON = Path(__file__).parent.parent / "teams.json"
_USER_JSON = config.DATA_DIR / "teams.json"


def _load_json(path: Path) -> tuple[list[dict], Path] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    markets = [m for m in data if isinstance(m, dict) and "nfl" in m]
    return (markets, path.parent) if markets else None


def _load_markets() -> tuple[list[dict], Path]:
    return (
        _load_json(_USER_JSON)
        or _load_json(_DEFAULT_JSON)
        or ([], Path(__file__).parent.parent)
    )


MARKETS: list[dict]
ICONS_BASE: Path
MARKETS, ICONS_BASE = _load_markets()

SPORTS: list[str] = ["nfl"] + sorted({
    k for m in MARKETS for k in m if k != "nfl"
})

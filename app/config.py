from pathlib import Path

from dotenv import dotenv_values

_ROOT = Path(__file__).parent.parent
_env = dotenv_values(_ROOT / ".env")


def _str(key: str, default: str) -> str:
    return _env.get(key, default)


def _int(key: str, default: int) -> int:
    v = _env.get(key)
    return int(v) if v is not None else default


def _float(key: str, default: float) -> float:
    v = _env.get(key)
    return float(v) if v is not None else default


DATA_DIR: Path = Path(_str("DATA_DIR", str(Path.home() / ".card-scanner")))
BACKEND_URL: str = _str("BACKEND_URL", "https://seashell-app-2rkwm.ondigitalocean.app")

CARD_OUTPUT_W: int = _int("CARD_OUTPUT_W", 750)
CARD_OUTPUT_H: int = _int("CARD_OUTPUT_H", 1050)
JPEG_QUALITY: int = _int("JPEG_QUALITY", 95)

CAMERA_INDEX: int = _int("CAMERA_INDEX", 0)
CAMERA_WIDTH: int = _int("CAMERA_WIDTH", 2560)
CAMERA_HEIGHT: int = _int("CAMERA_HEIGHT", 1440)

MIN_CARD_AREA_FRACTION: float = _float("MIN_CARD_AREA_FRACTION", 0.05)
MAX_CARD_AREA_FRACTION: float = _float("MAX_CARD_AREA_FRACTION", 0.90)
CARD_ASPECT_TARGET: float = _float("CARD_ASPECT_TARGET", 1.40)
CARD_ASPECT_TOLERANCE: float = _float("CARD_ASPECT_TOLERANCE", 0.35)

from pathlib import Path

DATA_DIR: Path = Path.home() / ".card-scanner"
BACKEND_URL: str = "http://localhost:5555"

CARD_OUTPUT_W: int = 750   # 2.5 in × 300 dpi
CARD_OUTPUT_H: int = 1050  # 3.5 in × 300 dpi
JPEG_QUALITY: int = 95

CAMERA_INDEX: int = 0

MIN_CARD_AREA_FRACTION: float = 0.05
MAX_CARD_AREA_FRACTION: float = 0.90
CARD_ASPECT_TARGET: float = 3.5 / 2.5  # 1.40
CARD_ASPECT_TOLERANCE: float = 0.35

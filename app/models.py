from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Photo:
    index: int
    filename: str
    name: str
    uploaded: bool = False
    team: str = ""
    price: str = ""
    rotation: int = 0  # display-only rotation in degrees clockwise (0/90/180/270); file on disk stays unrotated


@dataclass
class Series:
    series_id: str
    series_name: str
    status: Literal["scanning", "ready", "uploading", "uploaded"]
    photos: list[Photo] = field(default_factory=list)
    total_cards: int = 0
    default_price: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> Series:
        valid = {f.name for f in dataclasses.fields(Photo)}
        photos = [Photo(**{k: v for k, v in p.items() if k in valid})
                  for p in d.get("photos", [])]
        return cls(
            series_id=d["series_id"],
            series_name=d["series_name"],
            status=d["status"],
            photos=photos,
            total_cards=d.get("total_cards", 0),
            default_price=d.get("default_price", ""),
        )

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

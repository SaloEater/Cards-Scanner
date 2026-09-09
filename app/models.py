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
    photo_seq: int = 0

    def next_photo_seq(self) -> int:
        """Reserve the next file number for this series.

        Monotonic: a number is never handed out twice, even after photos are
        deleted. Naming files by list position meant a delete freed a name, and
        the next scan reused it — the backend stores objects under the name it
        is given and overwrites silently, so the re-sent file replaced the older
        card's image and left two photo rows sharing one URL.
        """
        seq = self.photo_seq
        self.photo_seq += 1
        return seq

    @classmethod
    def from_dict(cls, d: dict) -> Series:
        valid = {f.name for f in dataclasses.fields(Photo)}
        photos = [Photo(**{k: v for k, v in p.items() if k in valid})
                  for p in d.get("photos", [])]
        photo_seq = d.get("photo_seq")
        if photo_seq is None:
            # State written before the counter existed: resume past the highest
            # number it ever assigned. Filenames also carry a "c" prefix now, so
            # a new name cannot collide with a legacy bare-number one either way.
            photo_seq = max((p.index for p in photos), default=-1) + 1
        return cls(
            series_id=d["series_id"],
            series_name=d["series_name"],
            status=d["status"],
            photos=photos,
            total_cards=d.get("total_cards", 0),
            default_price=d.get("default_price", ""),
            photo_seq=photo_seq,
        )

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

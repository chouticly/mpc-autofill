import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.constants import CARDBACK_FILENAMES, IMAGE_EXTENSIONS
from src.decklist import normalise_card_name
from src.exc import ValidationException
from src.formatting import bold


@dataclass(frozen=True)
class LocalArtIndex:
    by_normalised_name: dict[str, str]
    cardback_path: Optional[str]
    all_image_paths: list[str]

    def find(self, card_name: str) -> Optional[str]:
        return self.by_normalised_name.get(normalise_card_name(card_name))

    def unused_images(self, used_paths: set[str]) -> list[str]:
        used = {os.path.abspath(path) for path in used_paths}
        unused: list[str] = []
        for path in self.all_image_paths:
            abs_path = os.path.abspath(path)
            if abs_path in used:
                continue
            if self.cardback_path and abs_path == os.path.abspath(self.cardback_path):
                continue
            unused.append(path)
        return unused


def index_local_art(working_directory: str) -> LocalArtIndex:
    by_name: dict[str, str] = {}
    all_images: list[str] = []
    cardback_path: Optional[str] = None

    for entry in sorted(os.listdir(working_directory)):
        path = os.path.join(working_directory, entry)
        if not os.path.isfile(path):
            continue
        suffix = Path(entry).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS:
            continue

        all_images.append(path)
        stem = Path(entry).stem
        normalised = normalise_card_name(stem)
        if normalised in CARDBACK_FILENAMES:
            cardback_path = path
            continue
        # First match wins for duplicate stems with different extensions
        by_name.setdefault(normalised, path)

    return LocalArtIndex(by_normalised_name=by_name, cardback_path=cardback_path, all_image_paths=all_images)


def require_cardback(index: LocalArtIndex) -> str:
    if index.cardback_path is None:
        raise ValidationException(
            "Decklist mode requires a common cardback image named "
            f"{bold('cardback.png')} (or .jpg / .jpeg / .webp) in the working directory."
        )
    return index.cardback_path

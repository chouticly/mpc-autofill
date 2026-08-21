import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.constants import CARDBACK_FILENAMES, IMAGE_EXTENSIONS
from src.decklist import normalise_card_name
from src.exc import ValidationException
from src.formatting import bold
from src.io import get_image_directory

_CACHED_DOWNLOAD_STEM = re.compile(r"^.+ \(.+\)$")


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


def _is_cached_download_stem(stem: str) -> bool:
    return _CACHED_DOWNLOAD_STEM.fullmatch(stem) is not None


def _image_files_in(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    paths: list[str] = []
    for entry in sorted(os.listdir(directory)):
        path = os.path.join(directory, entry)
        if os.path.isfile(path) and Path(entry).suffix.lower() in IMAGE_EXTENSIONS:
            paths.append(path)
    return paths


def index_local_art(working_directory: str) -> LocalArtIndex:
    by_name: dict[str, str] = {}
    custom_paths: list[str] = []
    cardback_path: Optional[str] = None
    cards_directory = get_image_directory(working_directory)

    def consider(path: str, include_as_custom: bool) -> None:
        nonlocal cardback_path
        stem = Path(path).stem
        normalised = normalise_card_name(stem)
        if normalised in CARDBACK_FILENAMES:
            if cardback_path is None:
                cardback_path = path
            return
        by_name.setdefault(normalised, path)
        if include_as_custom and not _is_cached_download_stem(stem):
            custom_paths.append(path)

    for path in _image_files_in(cards_directory):
        consider(path, include_as_custom=True)
    if os.path.abspath(working_directory) != os.path.abspath(cards_directory):
        for path in _image_files_in(working_directory):
            consider(path, include_as_custom=False)

    return LocalArtIndex(by_normalised_name=by_name, cardback_path=cardback_path, all_image_paths=custom_paths)


def require_cardback(index: LocalArtIndex) -> str:
    if index.cardback_path is None:
        raise ValidationException(
            "Decklist mode requires a common cardback image named "
            f"{bold('cardback.png')} (or .jpg / .jpeg / .webp) in the cards folder or working directory."
        )
    return index.cardback_path

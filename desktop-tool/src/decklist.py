import os
import re
import sys
from dataclasses import dataclass
from glob import glob
from typing import Optional

from InquirerPy import prompt

from src.constants import (
    CARD_TYPE_PREFIXES,
    CardTypes,
    DECKLIST_EXCLUDED_TXT_NAMES,
    DEFAULT_DECKLIST_FILENAME,
    FACE_SEPARATOR,
)
from src.exc import ValidationException
from src.formatting import bold
from src.logging import logger


@dataclass(frozen=True)
class DecklistFace:
    name: str
    card_type: str = CardTypes.CARD
    expansion_code: Optional[str] = None
    collector_number: Optional[str] = None


@dataclass(frozen=True)
class DecklistEntry:
    quantity: int
    front: DecklistFace
    back: Optional[DecklistFace] = None


def sanitise_whitespace(text: str) -> str:
    return re.sub(r" +", " ", text).strip()


def normalise_card_name(name: str) -> str:
    """Lowercase name with punctuation stripped for local-art matching."""
    cleaned = re.sub(r"[~`!@#$%^&*(){}\[\];:\"'’<,.>?/\\|_+=]", "", name.lower())
    return sanitise_whitespace(cleaned)


def _parse_face(raw: str) -> Optional[DecklistFace]:
    text = sanitise_whitespace(raw)
    if not text:
        return None

    prefix_pattern = "|".join(p for p in CARD_TYPE_PREFIXES if p)
    match = re.match(
        rf"^(?:({prefix_pattern}):)?(.+?)(?:\((.+)\)(.*))?$",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return DecklistFace(name=text)

    prefix = (match.group(1) or "").lower()
    name = sanitise_whitespace(match.group(2))
    expansion = match.group(3)
    collector = match.group(4)
    if not name:
        return None

    return DecklistFace(
        name=name,
        card_type=CARD_TYPE_PREFIXES.get(prefix, CardTypes.CARD),
        expansion_code=expansion.upper().strip() if expansion else None,
        collector_number=collector.strip() if collector and collector.strip() else None,
    )


def parse_decklist_line(line: str) -> Optional[DecklistEntry]:
    trimmed = sanitise_whitespace(line)
    if not trimmed or trimmed.startswith("#") or trimmed.startswith("//"):
        return None

    # Section headers like "Deck", "Sideboard", "Commander"
    if re.fullmatch(r"(deck|sideboard|commander|maybeboard|companion)", trimmed, flags=re.IGNORECASE):
        return None

    quantity_match = re.match(r"^([0-9]+[xX]?\s+)?(.*)$", trimmed)
    if quantity_match is None:
        return None

    quantity_raw = quantity_match.group(1)
    remainder = quantity_match.group(2)
    quantity = int(re.sub(r"[xX]", "", quantity_raw).strip()) if quantity_raw else 1
    if quantity <= 0 or not remainder:
        return None

    # Prefer " // " as face separator; allow bare "//" with surrounding spaces already normalised
    if f" {FACE_SEPARATOR} " in remainder:
        front_raw, back_raw = remainder.split(f" {FACE_SEPARATOR} ", 1)
    elif FACE_SEPARATOR in remainder and not remainder.strip().startswith(FACE_SEPARATOR):
        front_raw, back_raw = remainder.split(FACE_SEPARATOR, 1)
    else:
        front_raw, back_raw = remainder, None

    front = _parse_face(front_raw)
    if front is None:
        return None
    back = _parse_face(back_raw) if back_raw is not None else None
    return DecklistEntry(quantity=quantity, front=front, back=back)


def parse_decklist_text(text: str) -> list[DecklistEntry]:
    entries: list[DecklistEntry] = []
    for line in text.splitlines():
        entry = parse_decklist_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


def parse_decklist_file(file_path: str) -> list[DecklistEntry]:
    with open(file_path, encoding="utf-8") as f:
        entries = parse_decklist_text(f.read())
    if not entries:
        raise ValidationException(f"Decklist {bold(file_path)} did not contain any cards.")
    logger.info(f"Parsed {bold(len(entries))} line(s) from decklist {bold(os.path.basename(file_path))}.")
    return entries


def discover_decklist_paths(working_directory: str) -> list[str]:
    paths = sorted(
        path
        for path in glob(os.path.join(working_directory, "*.txt"))
        if os.path.basename(path).lower() not in DECKLIST_EXCLUDED_TXT_NAMES
    )
    return paths


def prompt_for_missing_decklist(working_directory: str) -> str:
    dest = os.path.join(working_directory, DEFAULT_DECKLIST_FILENAME)
    print(
        f"No decklist was found in {bold(working_directory)}.\n"
        f"Paste your decklist below, then enter a blank line when you are done.\n"
        f"It will be saved as {bold(DEFAULT_DECKLIST_FILENAME)}."
    )
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            if lines:
                break
            continue
        lines.append(line)

    text = "\n".join(lines).strip()
    if not parse_decklist_text(text):
        raise ValidationException("The pasted decklist did not contain any cards.")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    logger.info(f"Saved decklist to {bold(dest)}.")
    return dest


def select_decklist_paths(working_directory: str) -> list[str]:
    paths = discover_decklist_paths(working_directory=working_directory)
    if not paths:
        return [prompt_for_missing_decklist(working_directory=working_directory)]
    if len(paths) == 1:
        return paths

    questions = {
        "type": "list",
        "name": "decklist_choice",
        "message": (
            "Multiple decklist text files found. Please select any number of them to process.\n"
            "Select files by pressing Space, then confirm your selection by pressing Enter."
        ),
        "choices": paths,
        "multiselect": True,
    }
    answers = prompt(questions)
    selected = answers["decklist_choice"]
    if not selected:
        input("No decklists selected. Press enter to exit.")
        sys.exit(0)
    return selected

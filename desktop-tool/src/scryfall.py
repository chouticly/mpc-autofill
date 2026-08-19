from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import ratelimit
import requests

from src.constants import (
    SCRYFALL_ACCEPT,
    SCRYFALL_API_BASE,
    SCRYFALL_USER_AGENT,
    CardTypes,
)
from src.decklist import DecklistFace
from src.exc import ValidationException
from src.formatting import bold
from src.logging import logger


@dataclass(frozen=True)
class ScryfallFaceImages:
    name: str
    png_url: str


@dataclass(frozen=True)
class ScryfallCardImages:
    name: str
    scryfall_id: str
    faces: list[ScryfallFaceImages]


def _headers() -> dict[str, str]:
    return {"User-Agent": SCRYFALL_USER_AGENT, "Accept": SCRYFALL_ACCEPT}


@ratelimit.sleep_and_retry  # type: ignore  # `ratelimit` does not implement decorator typing correctly
@ratelimit.limits(calls=8, period=1)  # type: ignore  # `ratelimit` does not implement decorator typing correctly
def _scryfall_get(url: str, params: Optional[dict[str, Any]] = None) -> requests.Response:
    return requests.get(url, params=params or {}, headers=_headers(), timeout=30)


def _extract_png_faces(card: dict[str, Any]) -> list[ScryfallFaceImages]:
    image_uris = card.get("image_uris")
    if image_uris and image_uris.get("png"):
        return [ScryfallFaceImages(name=card.get("name", "Unknown"), png_url=image_uris["png"])]

    faces: list[ScryfallFaceImages] = []
    for face in card.get("card_faces") or []:
        face_uris = face.get("image_uris") or {}
        png = face_uris.get("png")
        if png:
            faces.append(ScryfallFaceImages(name=face.get("name", card.get("name", "Unknown")), png_url=png))
    return faces


def _card_images_from_json(card: dict[str, Any], query_label: str) -> ScryfallCardImages:
    faces = _extract_png_faces(card)
    if not faces:
        raise ValidationException(f"Scryfall card {bold(query_label)} has no PNG images.")
    return ScryfallCardImages(name=card.get("name", query_label), scryfall_id=card["id"], faces=faces)


def _card_from_response(response: requests.Response, query_label: str) -> ScryfallCardImages:
    if response.status_code == 404:
        raise ValidationException(f"No Scryfall card found for {bold(query_label)}.")
    if response.status_code != 200:
        raise ValidationException(
            f"Scryfall request failed for {bold(query_label)} with status {bold(response.status_code)}."
        )
    return _card_images_from_json(response.json(), query_label)


def _resolve_token(face: DecklistFace) -> ScryfallCardImages:
    search_url = f"{SCRYFALL_API_BASE}/cards/search"
    queries = [f'type:token !"{face.name}"', f"type:token {face.name}"]
    for query in queries:
        response = _scryfall_get(
            search_url,
            params={"q": query, "unique": "prints", "order": "released", "dir": "desc"},
        )
        if response.status_code != 200:
            continue
        data = response.json().get("data") or []
        if data:
            return _card_images_from_json(data[0], face.name)
    raise ValidationException(f"No Scryfall token found for {bold(face.name)}.")


def resolve_face(face: DecklistFace) -> ScryfallCardImages:
    """Resolve a decklist face to Scryfall PNG URL(s)."""
    # TODO(user follow-up): remind Nikolai to ask that missing/failed fetches skip the card instead of aborting
    if face.expansion_code and face.collector_number:
        set_code = face.expansion_code.lower()
        cn = quote(face.collector_number, safe="")
        url = f"{SCRYFALL_API_BASE}/cards/{set_code}/{cn}"
        label = f"{face.name} ({face.expansion_code}) {face.collector_number}"
        return _card_from_response(_scryfall_get(url), label)

    if face.card_type == CardTypes.TOKEN:
        return _resolve_token(face)

    named_url = f"{SCRYFALL_API_BASE}/cards/named"
    response = _scryfall_get(named_url, params={"exact": face.name})
    if response.status_code == 404:
        response = _scryfall_get(named_url, params={"fuzzy": face.name})
    return _card_from_response(response, face.name)


def download_png(png_url: str) -> bytes:
    response = _scryfall_get(png_url)
    if response.status_code != 200 or not response.content:
        raise ValidationException(f"Failed to download Scryfall image from {bold(png_url)}.")
    logger.debug(f"Downloaded Scryfall PNG ({len(response.content)} bytes).")
    return response.content

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Type
from urllib.parse import urljoin, urlparse

import requests
import sentry_sdk
from bulk_sync import bulk_sync

from cardpicker.models import (
    CanonicalArtist,
    CanonicalCard,
    CanonicalExpansion,
    DFCPair,
)
from cardpicker.schema_types import Face, Game, OfficialCardImage
from cardpicker.utils import section_timer


def default_is_response_valid(response: requests.Response) -> bool:
    return response.status_code == 200


def scryfall_png_url(scryfall_id: str, face: Face = Face.front) -> str:
    return f"https://cards.scryfall.io/png/{face.value}/{scryfall_id[0]}/{scryfall_id[1]}/{scryfall_id}.png"


def official_card_image(
    *,
    name: str,
    quantity: int,
    scryfall_id: str,
    face: Face = Face.front,
    expansion_code: str | None = None,
    collector_number: str | None = None,
) -> OfficialCardImage:
    if " // " in name:
        name = name.split(" // ", 1)[0] if face == Face.front else name.split(" // ", 1)[1]
    return OfficialCardImage(
        face=face,
        name=name,
        pngUrl=scryfall_png_url(scryfall_id, face),
        quantity=quantity,
        scryfallId=scryfall_id,
        collectorNumber=collector_number,
        expansionCode=expansion_code.upper() if expansion_code else None,
    )


@dataclass
class ImportedDecklist:
    cards: str
    official_images: list[OfficialCardImage] = field(default_factory=list)


class ImportSite(ABC):
    """
    Abstract base class for an import site integration. These should facilitate importing a list of cards
    based on a supplied URL.
    """

    @staticmethod
    @abstractmethod
    def get_host_names() -> list[str]:
        """
        Returns the host names for this import site, e.g. ["google.com", "www.google.com"].
        """

        ...

    @classmethod
    @abstractmethod
    def retrieve_card_list(cls, url: str) -> str | ImportedDecklist:
        """
        Takes a URL pointing to a card list hosted on this class's site, queries the site's API / whatever for
        the card list, formats it and returns it. May also include official Scryfall PNG URLs when available.
        """

        ...

    @classmethod
    def request(
        cls,
        path: str,
        method: str = "GET",
        is_response_valid: Callable[[requests.Response], bool] = default_is_response_valid,
        netloc: Optional[str] = None,
        headers: Optional[dict[str, Any]] = None,
    ) -> requests.Response:
        url = urljoin(f"https://{netloc or cls.get_host_names()[0]}", path)
        response = requests.request(url=url, method=method, headers=headers)
        if not is_response_valid(response):
            sentry_sdk.capture_message(response.text)
            raise cls.InvalidURLException(url)
        return response

    class InvalidURLException(Exception):
        def __init__(self, url: str):
            super().__init__(
                f"There was a problem with importing your list from {self.__class__.__name__} at URL {url}. "
                f"Check that your URL is correct and try again."
            )


class GameIntegration(ABC):
    """
    Abstract base class for a game integration. These collect code to integrate with a specific card game
    to enrich the app and tailor it more closely to the community's expectations.
    """

    # region abstract methods

    @classmethod
    @abstractmethod
    def get_game(cls) -> Game:
        ...

    @classmethod
    @abstractmethod
    def get_dfc_pairs(cls) -> list[DFCPair]:
        ...

    @classmethod
    @abstractmethod
    def get_import_sites(cls) -> list[Type[ImportSite]]:
        ...

    @classmethod
    @abstractmethod
    def get_canonical_cards_and_artists(
        cls,
        default_cards_path: Path | None = None,
        oracle_cards_path: Path | None = None,
    ) -> tuple[list[CanonicalCard], list[CanonicalArtist]]:
        ...

    @classmethod
    @abstractmethod
    def get_canonical_expansions(cls) -> list[CanonicalExpansion]:
        ...

    # endregion

    @classmethod
    def query_import_site(cls, url: Optional[str]) -> Optional[ImportedDecklist]:
        if url is None:
            raise ValueError("No decklist URL provided.")
        netloc = urlparse(url).netloc
        for site in cls.get_import_sites():
            if netloc in site.get_host_names():
                result = site.retrieve_card_list(url)
                if isinstance(result, str):
                    result = ImportedDecklist(cards=result)
                cleaned_text = "\n".join(
                    [stripped_line for line in result.cards.split("\n") if len(stripped_line := line.strip()) > 0]
                )
                if len(cleaned_text) > 0:
                    return ImportedDecklist(cards=cleaned_text, official_images=result.official_images)
        return None

    @classmethod
    def import_canonical_cards_and_artists(
        cls,
        default_cards_path: Path | None = None,
        oracle_cards_path: Path | None = None,
    ) -> None:
        @section_timer("retrieve_all_cards_and_identifiers")
        def retrieve_all_cards_and_identifiers() -> tuple[list[CanonicalCard], list[CanonicalArtist]]:
            return cls.get_canonical_cards_and_artists(
                default_cards_path=default_cards_path,
                oracle_cards_path=oracle_cards_path,
            )

        @section_timer("bulk_sync_canonical_artists")
        def bulk_sync_artists(artists_: list[CanonicalArtist]) -> None:
            print(f"Bulk syncing {len(artists_)} canonical artists...")
            bulk_sync(new_models=artists_, key_fields=["name"], db_class=CanonicalArtist, filters=None)

        @section_timer("add_canonical_cards")
        def add_cards(cards_: list[CanonicalCard]) -> None:
            print(f"Adding {len(cards_)} canonical cards...")
            CanonicalCard.objects.bulk_create(objs=cards_)

        cards, artists = retrieve_all_cards_and_identifiers()
        bulk_sync_artists(artists_=artists)
        add_cards(cards_=cards)

    @classmethod
    def import_canonical_expansions(cls) -> None:
        print("Retrieving all expansions...")
        t0 = time.time()
        expansions = cls.get_canonical_expansions()
        t1 = time.time()
        print(f"Retrieved {len(expansions)} expansions in {round(t1 - t0, 2)} seconds.")

        print("Beginning expansion bulk sync...")
        bulk_sync(new_models=expansions, key_fields=["identifier"], db_class=CanonicalExpansion, filters=None)
        t2 = time.time()
        print(f"Bulk synced expansions in {round(t2 - t1, 2)} seconds.")


__all__ = [
    "ImportSite",
    "GameIntegration",
    "ImportedDecklist",
    "official_card_image",
    "scryfall_png_url",
]

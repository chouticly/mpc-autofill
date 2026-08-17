import os
from pathlib import Path

from sanitize_filename import sanitize

from src.constants import Cardstocks, Faces, SourceType
from src.decklist import DecklistEntry, DecklistFace, parse_decklist_file, select_decklist_paths
from src.exc import ValidationException
from src.formatting import bold
from src.io import get_image_directory
from src.local_art import LocalArtIndex, index_local_art, require_cardback
from src.logging import logger
from src.order import CardImage, CardImageCollection, CardOrder, Details
from src.scryfall import ScryfallCardImages, resolve_face


def _local_card_image(working_directory: str, path: str, slots: set[int], query: str) -> CardImage:
    name = os.path.basename(path)
    image_directory = get_image_directory(working_directory=working_directory)
    return CardImage(
        drive_id=os.path.abspath(path),
        source_type=SourceType.LOCAL_FILE,
        slots=slots,
        name=name,
        file_path=os.path.join(image_directory, sanitize(name)),
        query=query,
    )


def _scryfall_card_image(
    working_directory: str, png_url: str, card_name: str, slots: set[int], query: str, unique_key: str
) -> CardImage:
    image_directory = get_image_directory(working_directory=working_directory)
    name = sanitize(f"{card_name} ({unique_key}).png")
    return CardImage(
        drive_id=png_url,
        source_type=SourceType.SCRYFALL,
        slots=slots,
        name=name,
        file_path=os.path.join(image_directory, name),
        query=query,
    )


def _resolve_face_images(face: DecklistFace) -> ScryfallCardImages:
    logger.info(f"Resolving {bold(face.name)} via Scryfall...")
    return resolve_face(face)


def _append_card(cards: dict[str, CardImage], card: CardImage) -> None:
    if card.drive_id in cards:
        cards[card.drive_id] = cards[card.drive_id].combine(card)
    else:
        cards[card.drive_id] = card


def _slots_for_quantity(start_slot: int, quantity: int) -> set[int]:
    return set(range(start_slot, start_slot + quantity))


def build_order_from_entries(
    entries: list[DecklistEntry],
    working_directory: str,
    local_art: LocalArtIndex,
    name: str,
) -> CardOrder:
    cardback_path = require_cardback(local_art)
    used_local_paths: set[str] = {os.path.abspath(cardback_path)}

    fronts: dict[str, CardImage] = {}
    backs: dict[str, CardImage] = {}
    next_slot = 0

    for entry in entries:
        slots = _slots_for_quantity(next_slot, entry.quantity)
        next_slot += entry.quantity

        local_front = local_art.find(entry.front.name)
        if local_front is not None:
            used_local_paths.add(os.path.abspath(local_front))
            _append_card(
                fronts,
                _local_card_image(
                    working_directory=working_directory, path=local_front, slots=slots, query=entry.front.name
                ),
            )
        else:
            scryfall_card = _resolve_face_images(entry.front)
            front_face = scryfall_card.faces[0]
            _append_card(
                fronts,
                _scryfall_card_image(
                    working_directory=working_directory,
                    png_url=front_face.png_url,
                    card_name=front_face.name,
                    slots=slots,
                    query=entry.front.name,
                    unique_key=f"{scryfall_card.scryfall_id}-0",
                ),
            )
            # Auto-back for DFCs when the decklist did not specify an explicit back
            if entry.back is None and len(scryfall_card.faces) > 1:
                back_face = scryfall_card.faces[1]
                _append_card(
                    backs,
                    _scryfall_card_image(
                        working_directory=working_directory,
                        png_url=back_face.png_url,
                        card_name=back_face.name,
                        slots=slots,
                        query=back_face.name,
                        unique_key=f"{scryfall_card.scryfall_id}-1",
                    ),
                )

        if entry.back is not None:
            local_back = local_art.find(entry.back.name)
            if local_back is not None:
                used_local_paths.add(os.path.abspath(local_back))
                _append_card(
                    backs,
                    _local_card_image(
                        working_directory=working_directory, path=local_back, slots=slots, query=entry.back.name
                    ),
                )
            else:
                scryfall_back = _resolve_face_images(entry.back)
                back_face = scryfall_back.faces[0]
                _append_card(
                    backs,
                    _scryfall_card_image(
                        working_directory=working_directory,
                        png_url=back_face.png_url,
                        card_name=back_face.name,
                        slots=slots,
                        query=entry.back.name,
                        unique_key=f"{scryfall_back.scryfall_id}-0",
                    ),
                )

    for custom_path in local_art.unused_images(used_local_paths):
        slots = {next_slot}
        next_slot += 1
        custom_name = Path(custom_path).stem
        logger.info(f"Including custom art with no decklist match: {bold(custom_name)}")
        _append_card(
            fronts,
            _local_card_image(
                working_directory=working_directory, path=custom_path, slots=slots, query=custom_name
            ),
        )

    quantity = next_slot
    if quantity <= 0:
        raise ValidationException("Decklist order is empty after resolving cards and custom art.")

    for card in list(fronts.values()) + list(backs.values()):
        card.validate()

    # Fill remaining back slots with the common cardback
    filled_back_slots = {slot for card in backs.values() for slot in card.slots}
    missing_back_slots = set(range(quantity)) - filled_back_slots
    if missing_back_slots:
        cardback = _local_card_image(
            working_directory=working_directory, path=cardback_path, slots=missing_back_slots, query="cardback"
        )
        cardback.validate()
        _append_card(backs, cardback)

    front_collection = CardImageCollection(cards_by_id=fronts, num_slots=quantity, face=Faces.front)
    back_collection = CardImageCollection(cards_by_id=backs, num_slots=quantity, face=Faces.back)
    front_collection.validate()
    back_collection.validate()

    return CardOrder(
        name=name,
        details=Details(
            quantity=quantity,
            stock=Cardstocks.S30.value,
            foil=False,
            allowed_to_exceed_project_max_size=True,
        ),
        fronts=front_collection,
        backs=back_collection,
    )


def orders_from_decklists_in_folder(working_directory: str) -> list[CardOrder]:
    paths = select_decklist_paths(working_directory=working_directory)
    if not paths:
        return []

    local_art = index_local_art(working_directory=working_directory)
    orders: list[CardOrder] = []
    for path in paths:
        entries = parse_decklist_file(path)
        order = build_order_from_entries(
            entries=entries,
            working_directory=working_directory,
            local_art=local_art,
            name=Path(path).stem,
        )
        logger.info(order.get_overview())
        orders.append(order)
    return orders

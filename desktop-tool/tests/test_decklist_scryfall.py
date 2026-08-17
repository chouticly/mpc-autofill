import io
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from src.constants import (
    DEFAULT_CARDBACK_DRIVE_ID,
    DEFAULT_DECKLIST_FILENAME,
    CardTypes,
    Cardstocks,
    ImageResizeMethods,
    MPC_BLEED_HEIGHT_AT_300_DPI,
    MPC_BLEED_WIDTH_AT_300_DPI,
    SourceType,
)
from src.decklist import (
    DecklistEntry,
    DecklistFace,
    discover_decklist_paths,
    normalise_card_name,
    parse_decklist_line,
    parse_decklist_text,
    prompt_for_missing_decklist,
    select_decklist_paths,
)
from src.exc import ValidationException
from src.local_art import index_local_art, require_cardback
from src.order_builder import (
    build_order_from_entries,
    orders_from_decklists_in_folder,
    parse_cardstock,
    prompt_cardstock_and_foil,
)
from src.processing import ImagePostProcessingConfig, post_process_image, target_dimensions
from src.scryfall import ScryfallCardImages, ScryfallFaceImages, resolve_face

FILE_PATH = os.path.abspath(os.path.dirname(__file__))


def _png_bytes(width: int, height: int, mode: str = "RGB", colour=(10, 20, 30)) -> bytes:
    if mode == "RGBA":
        img = Image.new("RGBA", (width, height), (*colour, 128))
    else:
        img = Image.new("RGB", (width, height), colour)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# region decklist parsing


def test_parse_simple_quantity_and_name():
    entry = parse_decklist_line("4x Lightning Bolt")
    assert entry == DecklistEntry(
        quantity=4,
        front=DecklistFace(name="Lightning Bolt", card_type=CardTypes.CARD),
    )


def test_parse_set_and_collector_number():
    entry = parse_decklist_line("1 Sol Ring (C21) 243")
    assert entry is not None
    assert entry.quantity == 1
    assert entry.front.name == "Sol Ring"
    assert entry.front.expansion_code == "C21"
    assert entry.front.collector_number == "243"


def test_parse_token_prefix():
    entry = parse_decklist_line("2 t:Treasure")
    assert entry is not None
    assert entry.front.card_type == CardTypes.TOKEN
    assert entry.front.name == "Treasure"


def test_parse_dual_face():
    entry = parse_decklist_line("1 Delver of Secrets // Insectile Aberration")
    assert entry is not None
    assert entry.front.name == "Delver of Secrets"
    assert entry.back is not None
    assert entry.back.name == "Insectile Aberration"


def test_parse_explicit_cardback_prefix():
    entry = parse_decklist_line("1 Lightning Bolt // b: Custom Back")
    assert entry is not None
    assert entry.front.name == "Lightning Bolt"
    assert entry.back is not None
    assert entry.back.name == "Custom Back"
    assert entry.back.card_type == CardTypes.CARDBACK


def test_parse_ignores_section_headers_and_comments():
    text = """
    Deck
    # comment
    1 Sol Ring
    Sideboard
    1 Negate
    """
    entries = parse_decklist_text(text)
    assert len(entries) == 2
    assert entries[0].front.name == "Sol Ring"
    assert entries[1].front.name == "Negate"


def test_normalise_card_name():
    assert normalise_card_name("Sol Ring!") == "sol ring"
    assert normalise_card_name("  Aether  Vial ") == "aether vial"


def test_discover_decklist_paths_excludes_logs(tmp_path: Path):
    (tmp_path / "deck.txt").write_text("1 Sol Ring\n", encoding="utf-8")
    (tmp_path / "autofill_log.txt").write_text("log\n", encoding="utf-8")
    paths = discover_decklist_paths(str(tmp_path))
    assert len(paths) == 1
    assert paths[0].endswith("deck.txt")


# endregion

# region local art


def test_index_local_art_finds_cardback_and_customs(tmp_path: Path):
    (tmp_path / "cardback.png").write_bytes(_png_bytes(10, 10))
    (tmp_path / "Sol Ring.png").write_bytes(_png_bytes(10, 10))
    (tmp_path / "Custom Hero.jpg").write_bytes(_png_bytes(10, 10))
    index = index_local_art(str(tmp_path))
    assert require_cardback(index).endswith("cardback.png")
    assert index.find("Sol Ring") is not None
    assert index.find("sol ring") is not None
    unused = index.unused_images({os.path.abspath(index.find("Sol Ring"))})
    assert any(path.endswith("Custom Hero.jpg") for path in unused)
    assert not any(path.endswith("cardback.png") for path in unused)


def test_require_cardback_raises_when_missing(tmp_path: Path):
    index = index_local_art(str(tmp_path))
    with pytest.raises(ValidationException):
        require_cardback(index)


# endregion

# region scryfall


def test_resolve_face_by_set_and_collector_number():
    face = DecklistFace(name="Sol Ring", expansion_code="C21", collector_number="243")
    card_json = {
        "id": "abc",
        "name": "Sol Ring",
        "image_uris": {"png": "https://example.com/sol.png"},
    }
    response = MagicMock(status_code=200)
    response.json.return_value = card_json
    with patch("src.scryfall._scryfall_get", return_value=response) as mock_get:
        result = resolve_face(face)
    assert result.scryfall_id == "abc"
    assert result.faces[0].png_url == "https://example.com/sol.png"
    assert "c21/243" in mock_get.call_args.args[0]


def test_resolve_face_named_exact_then_fuzzy():
    face = DecklistFace(name="Bolt")
    missing = MagicMock(status_code=404)
    found = MagicMock(status_code=200)
    found.json.return_value = {
        "id": "bolt-id",
        "name": "Lightning Bolt",
        "image_uris": {"png": "https://example.com/bolt.png"},
    }
    with patch("src.scryfall._scryfall_get", side_effect=[missing, found]) as mock_get:
        result = resolve_face(face)
    assert result.name == "Lightning Bolt"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].kwargs["params"] == {"exact": "Bolt"}
    assert mock_get.call_args_list[1].kwargs["params"] == {"fuzzy": "Bolt"}


def test_resolve_dfc_faces():
    face = DecklistFace(name="Delver of Secrets")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "id": "dfc-id",
        "name": "Delver of Secrets // Insectile Aberration",
        "card_faces": [
            {"name": "Delver of Secrets", "image_uris": {"png": "https://example.com/front.png"}},
            {"name": "Insectile Aberration", "image_uris": {"png": "https://example.com/back.png"}},
        ],
    }
    with patch("src.scryfall._scryfall_get", return_value=response):
        result = resolve_face(face)
    assert len(result.faces) == 2
    assert result.faces[1].name == "Insectile Aberration"


# endregion

# region image processing


def test_post_process_scryfall_sized_image_reaches_target_height():
    raw = _png_bytes(745, 1040, mode="RGBA")
    config = ImagePostProcessingConfig(max_dpi=800, downscale_alg=ImageResizeMethods.LANCZOS)
    processed = post_process_image(raw_image=raw, config=config)
    expected = target_dimensions(800)
    assert processed.size == expected
    assert processed.mode == "RGB"
    assert expected[1] == round(MPC_BLEED_HEIGHT_AT_300_DPI * 800 / 300)
    assert expected[0] == round(MPC_BLEED_WIDTH_AT_300_DPI * 800 / 300)


def test_post_process_downscales_oversized_image():
    raw = _png_bytes(4000, 5600)
    config = ImagePostProcessingConfig(max_dpi=100, downscale_alg=ImageResizeMethods.LANCZOS)
    processed = post_process_image(raw_image=raw, config=config)
    assert processed.size == target_dimensions(100)


# endregion

# region order builder


def test_build_order_local_override_and_unmatched_custom(tmp_path: Path):
    (tmp_path / "cardback.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "Sol Ring.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "My Custom.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "cards").mkdir()

    entries = [
        DecklistEntry(quantity=2, front=DecklistFace(name="Sol Ring")),
        DecklistEntry(quantity=1, front=DecklistFace(name="Lightning Bolt")),
    ]

    scryfall_bolt = ScryfallCardImages(
        name="Lightning Bolt",
        scryfall_id="bolt-id",
        faces=[ScryfallFaceImages(name="Lightning Bolt", png_url="https://example.com/bolt.png")],
    )

    with patch("src.order_builder.resolve_face", return_value=scryfall_bolt) as mock_resolve:
        order = build_order_from_entries(
            entries=entries,
            working_directory=str(tmp_path),
            local_art=index_local_art(str(tmp_path)),
            name="test-deck",
        )

    assert mock_resolve.call_count == 1
    assert mock_resolve.call_args.args[0].name == "Lightning Bolt"

    assert order.details.quantity == 4  # 2 Sol Ring + 1 Bolt + 1 custom
    front_names = {card.name for card in order.fronts.cards_by_id.values()}
    assert "Sol Ring.png" in front_names
    assert "My Custom.png" in front_names
    assert any(card.source_type == SourceType.SCRYFALL for card in order.fronts.cards_by_id.values())
    assert any(card.source_type == SourceType.LOCAL_FILE and card.name == "Sol Ring.png" for card in order.fronts.cards_by_id.values())

    sol_ring = next(card for card in order.fronts.cards_by_id.values() if card.name == "Sol Ring.png")
    assert sol_ring.slots == {0, 1}

    cardback = next(card for card in order.backs.cards_by_id.values() if card.name == "cardback.png")
    assert cardback.slots == {0, 1, 2, 3}
    assert cardback.source_type == SourceType.LOCAL_FILE
    assert cardback.query == "cardback"
    assert not any(card.name == "cardback.png" for card in order.fronts.cards_by_id.values())


def test_default_cardback_used_when_local_file_missing(tmp_path: Path):
    (tmp_path / "Sol Ring.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "cards").mkdir()
    entries = [DecklistEntry(quantity=2, front=DecklistFace(name="Sol Ring"))]
    order = build_order_from_entries(
        entries=entries,
        working_directory=str(tmp_path),
        local_art=index_local_art(str(tmp_path)),
        name="default-back",
    )
    assert len(order.backs.cards_by_id) == 1
    cardback = next(iter(order.backs.cards_by_id.values()))
    assert cardback.drive_id == DEFAULT_CARDBACK_DRIVE_ID
    assert cardback.source_type == SourceType.GOOGLE_DRIVE
    assert cardback.slots == {0, 1}


def test_jpeg_cardback_fills_all_slots(tmp_path: Path):
    (tmp_path / "cardback.jpg").write_bytes(_png_bytes(20, 30))
    (tmp_path / "Sol Ring.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "cards").mkdir()
    entries = [DecklistEntry(quantity=3, front=DecklistFace(name="Sol Ring"))]
    order = build_order_from_entries(
        entries=entries,
        working_directory=str(tmp_path),
        local_art=index_local_art(str(tmp_path)),
        name="jpg-back",
    )
    assert len(order.backs.cards_by_id) == 1
    cardback = next(iter(order.backs.cards_by_id.values()))
    assert cardback.name == "cardback.jpg"
    assert cardback.slots == {0, 1, 2}


def test_all_slots_share_one_cardback(tmp_path: Path):
    (tmp_path / "cardback.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "Sol Ring.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "cards").mkdir()

    entries = [
        DecklistEntry(quantity=1, front=DecklistFace(name="Sol Ring")),
        DecklistEntry(
            quantity=1,
            front=DecklistFace(name="Lightning Bolt"),
            back=DecklistFace(name="Custom Back", card_type=CardTypes.CARDBACK),
        ),
        DecklistEntry(quantity=1, front=DecklistFace(name="Delver of Secrets")),
    ]
    bolt = ScryfallCardImages(
        name="Lightning Bolt",
        scryfall_id="bolt-id",
        faces=[ScryfallFaceImages(name="Lightning Bolt", png_url="https://example.com/bolt.png")],
    )
    delver = ScryfallCardImages(
        name="Delver of Secrets // Insectile Aberration",
        scryfall_id="dfc-id",
        faces=[
            ScryfallFaceImages(name="Delver of Secrets", png_url="https://example.com/delver.png"),
            ScryfallFaceImages(name="Insectile Aberration", png_url="https://example.com/insect.png"),
        ],
    )

    def fake_resolve(face: DecklistFace) -> ScryfallCardImages:
        if face.name == "Lightning Bolt":
            return bolt
        if face.name == "Delver of Secrets":
            return delver
        raise AssertionError(f"unexpected Scryfall lookup: {face.name}")

    with patch("src.order_builder.resolve_face", side_effect=fake_resolve):
        order = build_order_from_entries(
            entries=entries,
            working_directory=str(tmp_path),
            local_art=index_local_art(str(tmp_path)),
            name="shared-back",
        )

    assert len(order.backs.cards_by_id) == 1
    cardback = next(iter(order.backs.cards_by_id.values()))
    assert cardback.name == "cardback.png"
    assert cardback.slots == {0, 1, 2}
    assert not any("Insectile" in (card.name or "") for card in order.backs.cards_by_id.values())
    assert not any("Insectile" in (card.name or "") for card in order.fronts.cards_by_id.values())


def test_build_order_uses_requested_stock_and_foil(tmp_path: Path):
    (tmp_path / "Sol Ring.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "cards").mkdir()
    order = build_order_from_entries(
        entries=[DecklistEntry(quantity=1, front=DecklistFace(name="Sol Ring"))],
        working_directory=str(tmp_path),
        local_art=index_local_art(str(tmp_path)),
        name="foil-order",
        stock=Cardstocks.S33.value,
        foil=True,
    )
    assert order.details.stock == Cardstocks.S33.value
    assert order.details.foil is True


def test_parse_cardstock_accepts_name_or_value():
    assert parse_cardstock("S30") == Cardstocks.S30.value
    assert parse_cardstock(Cardstocks.M31.value) == Cardstocks.M31.value


def test_prompt_cardstock_and_foil_skips_when_provided():
    stock, foil = prompt_cardstock_and_foil(stock="S27", foil=True)
    assert stock == Cardstocks.S27.value
    assert foil is True


def test_plastic_cardstock_cannot_be_foil():
    with pytest.raises(ValidationException, match="foil"):
        prompt_cardstock_and_foil(stock="P10", foil=True)


def test_plastic_cardstock_skips_foil_prompt():
    stock, foil = prompt_cardstock_and_foil(stock="P10", foil=None)
    assert stock == Cardstocks.P10.value
    assert foil is False


def test_prompt_for_missing_decklist_writes_decklist_txt(tmp_path: Path, monkeypatch):
    answers = iter(["1 Sol Ring", "2 Lightning Bolt", ""])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    path = prompt_for_missing_decklist(str(tmp_path))
    assert os.path.basename(path) == DEFAULT_DECKLIST_FILENAME
    text = Path(path).read_text(encoding="utf-8")
    assert "Sol Ring" in text
    assert "Lightning Bolt" in text


def test_select_decklist_paths_prompts_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "src.decklist.prompt_for_missing_decklist",
        lambda working_directory: os.path.join(working_directory, DEFAULT_DECKLIST_FILENAME),
    )
    paths = select_decklist_paths(str(tmp_path))
    assert paths == [os.path.join(str(tmp_path), DEFAULT_DECKLIST_FILENAME)]


def test_orders_from_decklist_without_prompting(tmp_path: Path):
    (tmp_path / "deck.txt").write_text("1 Sol Ring\n", encoding="utf-8")
    (tmp_path / "Sol Ring.png").write_bytes(_png_bytes(20, 30))
    (tmp_path / "cards").mkdir()
    orders = orders_from_decklists_in_folder(str(tmp_path), stock="S30", foil=False)
    assert len(orders) == 1
    assert orders[0].details.stock == Cardstocks.S30.value
    assert orders[0].details.foil is False


# endregion

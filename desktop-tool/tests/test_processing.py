import io

from PIL import Image

from src.constants import (
    MPC_BLEED_HEIGHT_AT_300_DPI,
    MPC_BLEED_PIXELS_PER_SIDE_AT_300_DPI,
    MPC_BLEED_WIDTH_AT_300_DPI,
    MPC_TRIM_HEIGHT_AT_300_DPI,
    MPC_TRIM_WIDTH_AT_300_DPI,
    ImageResizeMethods,
)
from src.processing import (
    ImagePostProcessingConfig,
    bleed_pixels_per_side,
    post_process_image,
    target_dimensions,
    trim_dimensions,
)

BORDER = (20, 20, 20)
FILL = (0, 80, 255)
NEAREST_300 = ImagePostProcessingConfig(max_dpi=300, downscale_alg=ImageResizeMethods.NEAREST)


def _png_bytes(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _card_with_border(width: int, height: int, border_px: int) -> Image.Image:
    img = Image.new("RGB", (width, height), BORDER)
    inner_width = width - 2 * border_px
    inner_height = height - 2 * border_px
    img.paste(Image.new("RGB", (inner_width, inner_height), FILL), (border_px, border_px))
    return img


def _strip_contains_colour(img: Image.Image, box: tuple[int, int, int, int], colour: tuple[int, int, int]) -> bool:
    return colour in img.crop(box).getdata()


def test_trim_constants_match_bleed_canvas() -> None:
    assert MPC_TRIM_WIDTH_AT_300_DPI == MPC_BLEED_WIDTH_AT_300_DPI - 2 * MPC_BLEED_PIXELS_PER_SIDE_AT_300_DPI
    assert MPC_TRIM_HEIGHT_AT_300_DPI == MPC_BLEED_HEIGHT_AT_300_DPI - 2 * MPC_BLEED_PIXELS_PER_SIDE_AT_300_DPI
    assert trim_dimensions(300) == (MPC_TRIM_WIDTH_AT_300_DPI, MPC_TRIM_HEIGHT_AT_300_DPI)
    assert bleed_pixels_per_side(300) == MPC_BLEED_PIXELS_PER_SIDE_AT_300_DPI


def test_trim_sized_image_fits_inside_cut_and_bleeds_outward() -> None:
    source = _card_with_border(745, 1040, border_px=20)
    processed = post_process_image(raw_image=_png_bytes(source), config=NEAREST_300)
    assert processed.size == target_dimensions(300)

    bleed = bleed_pixels_per_side(300)
    width, height = processed.size
    assert not _strip_contains_colour(processed, (0, 0, bleed, height), FILL)
    assert not _strip_contains_colour(processed, (width - bleed, 0, width, height), FILL)
    assert not _strip_contains_colour(processed, (0, 0, width, bleed), FILL)
    assert not _strip_contains_colour(processed, (0, height - bleed, width, height), FILL)
    assert processed.getpixel((width // 2, height // 2)) == FILL
    assert processed.getpixel((0, 0)) == BORDER
    assert processed.getpixel((bleed - 1, height // 2)) == BORDER


def test_already_bled_image_keeps_existing_inset() -> None:
    bleed = MPC_BLEED_PIXELS_PER_SIDE_AT_300_DPI
    source = _card_with_border(MPC_BLEED_WIDTH_AT_300_DPI, MPC_BLEED_HEIGHT_AT_300_DPI, border_px=bleed)
    processed = post_process_image(raw_image=_png_bytes(source), config=NEAREST_300)
    assert processed.size == target_dimensions(300)

    assert processed.getpixel((bleed, bleed)) == FILL
    assert processed.getpixel((bleed - 1, bleed)) == BORDER
    assert processed.getpixel((0, 0)) == BORDER
    assert processed.getpixel((processed.width // 2, processed.height // 2)) == FILL


def test_trim_sized_image_at_max_dpi_still_keeps_fill_out_of_bleed() -> None:
    source = _card_with_border(745, 1040, border_px=20)
    config = ImagePostProcessingConfig(max_dpi=800, downscale_alg=ImageResizeMethods.NEAREST)
    processed = post_process_image(raw_image=_png_bytes(source), config=config)
    assert processed.size == target_dimensions(800)

    bleed = bleed_pixels_per_side(800)
    width, height = processed.size
    assert not _strip_contains_colour(processed, (0, 0, bleed, height), FILL)
    assert processed.getpixel((width // 2, height // 2)) == FILL

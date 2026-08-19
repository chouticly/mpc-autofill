import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.constants import (
    EMBEDDED_PRINT_DPI,
    MIN_PRINT_DPI,
    MPC_BLEED_HEIGHT_AT_300_DPI,
    MPC_BLEED_WIDTH_AT_300_DPI,
    ImageResizeMethods,
)

if TYPE_CHECKING:
    from PIL import Image


@dataclass
class ImagePostProcessingConfig:
    max_dpi: int
    downscale_alg: ImageResizeMethods


def target_dimensions(max_dpi: int) -> tuple[int, int]:
    scale = max_dpi / 300
    width = round(MPC_BLEED_WIDTH_AT_300_DPI * scale)
    height = round(MPC_BLEED_HEIGHT_AT_300_DPI * scale)
    return width, height


def embedded_file_dpi(max_dpi: int) -> int:
    return max(max_dpi, EMBEDDED_PRINT_DPI)


def _flatten_alpha(img: "Image.Image") -> "Image.Image":
    from PIL import Image

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        composited = Image.alpha_composite(background, rgba)
        return composited.convert("RGB")
    return img.convert("RGB")


def _pad_to_mpc_aspect(img: "Image.Image") -> "Image.Image":
    """Pad (edge-extend) an image to the MPC card+bleed aspect ratio."""
    from PIL import Image

    target_ratio = MPC_BLEED_WIDTH_AT_300_DPI / MPC_BLEED_HEIGHT_AT_300_DPI
    width, height = img.size
    current_ratio = width / height

    if abs(current_ratio - target_ratio) < 1e-3:
        return img

    if current_ratio > target_ratio:
        # Too wide — pad top/bottom
        new_height = round(width / target_ratio)
        pad_y = max(0, (new_height - height) // 2)
        padded = Image.new("RGB", (width, new_height), (0, 0, 0))
        padded.paste(img, (0, pad_y))
        # Fill remaining vertical bands by extending edge rows
        if pad_y > 0:
            top = img.crop((0, 0, width, 1)).resize((width, pad_y))
            padded.paste(top, (0, 0))
            bottom_height = new_height - height - pad_y
            if bottom_height > 0:
                bottom = img.crop((0, height - 1, width, height)).resize((width, bottom_height))
                padded.paste(bottom, (0, height + pad_y))
        return padded

    # Too tall — pad left/right
    new_width = round(height * target_ratio)
    pad_x = max(0, (new_width - width) // 2)
    padded = Image.new("RGB", (new_width, height), (0, 0, 0))
    padded.paste(img, (pad_x, 0))
    if pad_x > 0:
        left = img.crop((0, 0, 1, height)).resize((pad_x, height))
        padded.paste(left, (0, 0))
        right_width = new_width - width - pad_x
        if right_width > 0:
            right = img.crop((width - 1, 0, width, height)).resize((right_width, height))
            padded.paste(right, (width + pad_x, 0))
    return padded


def post_process_image(raw_image: bytes, config: ImagePostProcessingConfig) -> "Image":
    from PIL import Image

    img = Image.open(io.BytesIO(raw_image))
    img = _flatten_alpha(img)
    img = _pad_to_mpc_aspect(img)

    target_width, target_height = target_dimensions(config.max_dpi)
    if img.size != (target_width, target_height):
        img = img.resize((target_width, target_height), config.downscale_alg.value)

    return img


def save_processed_image(img: "Image.Image", file_path: str, dpi: int) -> None:
    suffix = Path(file_path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        img.save(file_path, dpi=(dpi, dpi), quality=95, subsampling=0)
    else:
        img.save(file_path, dpi=(dpi, dpi))


def _image_dpi_meets_minimum(img: "Image.Image") -> bool:
    dpi = img.info.get("dpi")
    if not dpi:
        return False
    try:
        return float(dpi[0]) >= MIN_PRINT_DPI - 0.05 and float(dpi[1]) >= MIN_PRINT_DPI - 0.05
    except (TypeError, ValueError, IndexError):
        return False


def image_meets_mpc_print_requirements(file_path: str, config: Optional[ImagePostProcessingConfig]) -> bool:
    if not file_path or not os.path.isfile(file_path) or os.path.getsize(file_path) <= 0:
        return False

    from PIL import Image

    with Image.open(file_path) as img:
        min_width, min_height = target_dimensions(MIN_PRINT_DPI)
        if img.size[0] < min_width or img.size[1] < min_height:
            return False
        if config is None:
            return True
        return img.size == target_dimensions(config.max_dpi) and _image_dpi_meets_minimum(img)

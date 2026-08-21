import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.constants import (
    EMBEDDED_PRINT_DPI,
    MIN_PRINT_DPI,
    MPC_BLEED_HEIGHT_AT_300_DPI,
    MPC_BLEED_PIXELS_PER_SIDE_AT_300_DPI,
    MPC_BLEED_WIDTH_AT_300_DPI,
    MPC_TRIM_HEIGHT_AT_300_DPI,
    MPC_TRIM_WIDTH_AT_300_DPI,
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


def bleed_pixels_per_side(max_dpi: int) -> int:
    return round(MPC_BLEED_PIXELS_PER_SIDE_AT_300_DPI * max_dpi / 300)


def trim_dimensions(max_dpi: int) -> tuple[int, int]:
    width, height = target_dimensions(max_dpi)
    bleed = bleed_pixels_per_side(max_dpi)
    return width - 2 * bleed, height - 2 * bleed


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


def _image_is_trim_sized(img: "Image.Image") -> bool:
    current_ratio = img.width / img.height
    trim_ratio = MPC_TRIM_WIDTH_AT_300_DPI / MPC_TRIM_HEIGHT_AT_300_DPI
    bleed_ratio = MPC_BLEED_WIDTH_AT_300_DPI / MPC_BLEED_HEIGHT_AT_300_DPI
    return abs(current_ratio - trim_ratio) < abs(current_ratio - bleed_ratio)


def _scale_to_fit_within(img: "Image.Image", box_width: int, box_height: int, resample: int) -> "Image.Image":
    scale = min(box_width / img.width, box_height / img.height)
    new_width = max(1, round(img.width * scale))
    new_height = max(1, round(img.height * scale))
    if (new_width, new_height) == img.size:
        return img
    return img.resize((new_width, new_height), resample)


def _paste_centered_with_edge_extend(img: "Image.Image", canvas_size: tuple[int, int]) -> "Image.Image":
    from PIL import Image

    canvas_width, canvas_height = canvas_size
    image_width, image_height = img.size
    offset_x = (canvas_width - image_width) // 2
    offset_y = (canvas_height - image_height) // 2
    if img.size == canvas_size:
        return img

    canvas = Image.new("RGB", canvas_size, (0, 0, 0))
    canvas.paste(img, (offset_x, offset_y))

    if offset_y > 0:
        top = img.crop((0, 0, image_width, 1)).resize((image_width, offset_y))
        canvas.paste(top, (offset_x, 0))
        bottom_height = canvas_height - image_height - offset_y
        if bottom_height > 0:
            bottom = img.crop((0, image_height - 1, image_width, image_height)).resize((image_width, bottom_height))
            canvas.paste(bottom, (offset_x, offset_y + image_height))

    if offset_x > 0:
        left = canvas.crop((offset_x, 0, offset_x + 1, canvas_height)).resize((offset_x, canvas_height))
        canvas.paste(left, (0, 0))
        right_width = canvas_width - image_width - offset_x
        if right_width > 0:
            right_x = offset_x + image_width - 1
            right = canvas.crop((right_x, 0, right_x + 1, canvas_height)).resize((right_width, canvas_height))
            canvas.paste(right, (offset_x + image_width, 0))

    return canvas


def _pad_to_mpc_aspect(img: "Image.Image") -> "Image.Image":
    target_ratio = MPC_BLEED_WIDTH_AT_300_DPI / MPC_BLEED_HEIGHT_AT_300_DPI
    width, height = img.size
    current_ratio = width / height

    if abs(current_ratio - target_ratio) < 1e-3:
        return img

    if current_ratio > target_ratio:
        return _paste_centered_with_edge_extend(img, (width, round(width / target_ratio)))
    return _paste_centered_with_edge_extend(img, (round(height * target_ratio), height))


def post_process_image(raw_image: bytes, config: ImagePostProcessingConfig) -> "Image":
    from PIL import Image

    img = Image.open(io.BytesIO(raw_image))
    img = _flatten_alpha(img)
    target_width, target_height = target_dimensions(config.max_dpi)

    if _image_is_trim_sized(img):
        trim_width, trim_height = trim_dimensions(config.max_dpi)
        img = _scale_to_fit_within(img, trim_width, trim_height, config.downscale_alg.value)
        return _paste_centered_with_edge_extend(img, (target_width, target_height))

    img = _pad_to_mpc_aspect(img)
    if img.size != (target_width, target_height):
        img = img.resize((target_width, target_height), config.downscale_alg.value)

    return img


def _exif_with_dpi(dpi: int) -> bytes:
    from PIL import Image
    from PIL.ExifTags import Base

    exif = Image.Exif()
    exif[Base.XResolution] = dpi
    exif[Base.YResolution] = dpi
    exif[Base.ResolutionUnit] = 2
    return exif.tobytes()


def save_processed_image(img: "Image.Image", file_path: str, dpi: int) -> None:
    suffix = Path(file_path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        img.save(file_path, format="JPEG", dpi=(dpi, dpi), quality=95, subsampling=0)
    elif suffix == ".webp":
        img.save(file_path, format="WEBP", lossless=True, exif=_exif_with_dpi(dpi))
    else:
        img.save(file_path, dpi=(dpi, dpi))


def _image_dpi_meets_minimum(img: "Image.Image") -> bool:
    dpi = img.info.get("dpi")
    if not dpi:
        from PIL.ExifTags import Base

        exif = img.getexif()
        x_res = exif.get(Base.XResolution)
        y_res = exif.get(Base.YResolution)
        if x_res is None or y_res is None:
            return False
        dpi = (x_res, y_res)
    try:
        return float(dpi[0]) >= MIN_PRINT_DPI - 0.05 and float(dpi[1]) >= MIN_PRINT_DPI - 0.05
    except (TypeError, ValueError, IndexError):
        return False


def image_meets_mpc_print_requirements(file_path: str, config: Optional[ImagePostProcessingConfig]) -> bool:
    if not file_path or not os.path.isfile(file_path) or os.path.getsize(file_path) <= 0:
        return False

    from PIL import Image

    with Image.open(file_path) as img:
        if config is None:
            min_width, min_height = target_dimensions(MIN_PRINT_DPI)
            return img.size[0] >= min_width and img.size[1] >= min_height

        if img.size != target_dimensions(config.max_dpi):
            return False
        if config.max_dpi < MIN_PRINT_DPI:
            return True
        return _image_dpi_meets_minimum(img)

"""Decoding, validation and normalisation of uploaded photographs (Pillow)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageCms, ImageOps

ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF"}
THUMB_EDGE = 480
WORK_EDGE = 2048  # long edge of previews, masks and the brush editor


class ImageRejected(Exception):
    """The file is not an image we can safely use. Message is user-facing."""


@dataclass
class ImageInfo:
    format: str
    width: int  # after EXIF orientation
    height: int
    mode: str
    has_icc: bool
    orientation: int
    warnings: list[str] = field(default_factory=list)


def configure(max_megapixels: int) -> None:
    # Pillow raises DecompressionBombError above 2x this value; we check
    # the exact limit ourselves in probe().
    Image.MAX_IMAGE_PIXELS = max_megapixels * 1_000_000


def probe(data: bytes, max_megapixels: int) -> ImageInfo:
    """Cheap header-level validation. Does not decode all pixels."""
    if not data:
        raise ImageRejected("The file is empty.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            if fmt not in ACCEPTED_FORMATS:
                raise ImageRejected("Use a JPEG, PNG, WebP or TIFF image.")
            w, h = im.size
            if w < 16 or h < 16:
                raise ImageRejected("The image is too small.")
            if w * h > max_megapixels * 1_000_000:
                raise ImageRejected(f"The image is larger than {max_megapixels} megapixels.")
            orientation = int(im.getexif().get(0x0112, 1) or 1)
            mode = im.mode
            has_icc = bool(im.info.get("icc_profile"))
            if getattr(im, "n_frames", 1) > 1 and fmt != "TIFF":
                raise ImageRejected("Animated images are not supported.")
            im.verify()
    except ImageRejected:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageRejected(f"The image is larger than {max_megapixels} megapixels.") from exc
    except Exception as exc:
        raise ImageRejected("This file could not be read as an image.") from exc

    if orientation in (5, 6, 7, 8):
        w, h = h, w
    warnings = []
    if mode not in ("RGB", "L"):
        warnings.append(f"Colour mode {mode} is converted to 8-bit RGB for editing.")
    return ImageInfo(fmt, w, h, mode, has_icc, orientation, warnings)


SRGB = ImageCms.createProfile("sRGB")


def load_rgb(data: bytes) -> tuple[Image.Image, bytes | None, Image.Exif]:
    """Decode fully, apply EXIF orientation, return (RGB image, ICC bytes, EXIF).

    Pixel values are not colour-converted, so an untouched original keeps its
    exact values; the ICC profile is carried alongside.
    """
    with Image.open(io.BytesIO(data)) as raw:
        raw.load()
        icc = raw.info.get("icc_profile")
        exif = raw.getexif()
        im = ImageOps.exif_transpose(raw)
    if im.mode == "I;16" or im.mode.startswith("I;16"):
        im = im.point(lambda v: v / 256).convert("L")
    if im.mode in ("RGBA", "LA", "P", "PA"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        im = bg
    elif im.mode == "CMYK" and icc:
        try:
            im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)), SRGB, outputMode="RGB")
            icc = None
        except Exception:
            im = im.convert("RGB")
    elif im.mode != "RGB":
        im = im.convert("RGB")
    exif[0x0112] = 1
    return im, icc, exif


def to_profile(im: Image.Image, src_icc: bytes | None, dst_icc: bytes | None) -> Image.Image:
    """Convert `im` from its profile to the destination profile (sRGB when absent)."""
    if src_icc == dst_icc:
        return im
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(src_icc)) if src_icc else SRGB
        dst = ImageCms.ImageCmsProfile(io.BytesIO(dst_icc)) if dst_icc else SRGB
        return ImageCms.profileToProfile(im, src, dst, outputMode="RGB")
    except Exception:
        return im


def fit_size(width: int, height: int, edge: int) -> tuple[int, int]:
    """Largest size with long edge <= `edge`, keeping aspect ratio (never upscales)."""
    scale = min(1.0, edge / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def work_size(width: int, height: int) -> tuple[int, int]:
    return fit_size(width, height, WORK_EDGE)


def encode_jpeg(im: Image.Image, quality: int = 88, icc: bytes | None = None, exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    kwargs: dict = {"quality": quality, "optimize": True}
    if icc:
        kwargs["icc_profile"] = icc
    if exif:
        kwargs["exif"] = exif
    im.convert("RGB").save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def encode_png(im: Image.Image, icc: bytes | None = None, exif: bytes | None = None, level: int = 6) -> bytes:
    buf = io.BytesIO()
    kwargs: dict = {"compress_level": level}
    if icc:
        kwargs["icc_profile"] = icc
    if exif:
        kwargs["exif"] = exif
    im.save(buf, "PNG", **kwargs)
    return buf.getvalue()


def derivatives(im: Image.Image, icc: bytes | None) -> dict[str, tuple[bytes, int, int]]:
    """Thumbnail and work-size preview of an (already oriented) image.

    The preview is converted to sRGB so browsers show it consistently.
    """
    srgb = to_profile(im, icc, None)
    out = {}
    tw, th = fit_size(*im.size, THUMB_EDGE)
    out["thumbnail"] = (encode_jpeg(srgb.resize((tw, th), Image.Resampling.LANCZOS), 82), tw, th)
    pw, ph = work_size(*im.size)
    out["preview"] = (encode_jpeg(srgb.resize((pw, ph), Image.Resampling.LANCZOS), 90), pw, ph)
    return out

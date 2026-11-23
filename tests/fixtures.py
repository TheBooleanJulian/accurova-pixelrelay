"""Small synthetic photographs for tests (deterministic, no files on disk)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def textured_photo(width: int = 900, height: int = 600, seed: int = 7) -> Image.Image:
    """A 'photo' with gradients, shapes and fine texture so alignment has signal."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    r = 90 + 80 * np.sin(x / 57.0) + 30 * np.cos(y / 23.0)
    g = 110 + 70 * np.cos((x + y) / 71.0)
    b = 120 + 60 * np.sin(y / 41.0) * np.cos(x / 97.0)
    base = np.stack([r, g, b], axis=-1)
    noise = rng.normal(0, 18, (height // 4 + 1, width // 4 + 1, 3))
    noise = np.kron(noise, np.ones((4, 4, 1)))[:height, :width]
    arr = np.clip(base + noise, 0, 255).astype(np.uint8)
    im = Image.fromarray(arr, "RGB").filter(ImageFilter.GaussianBlur(0.8))
    d = ImageDraw.Draw(im)
    for i in range(14):
        cx, cy = rng.integers(0, width), rng.integers(0, height)
        rad = int(rng.integers(12, 60))
        col = tuple(int(v) for v in rng.integers(20, 235, 3))
        if i % 2:
            d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=col)
        else:
            d.rectangle((cx - rad, cy - rad // 2, cx + rad, cy + rad // 2), fill=col)
    return im


EDIT_BOX = (300, 180, 480, 330)  # region changed in `edited_copy`, in base pixels


def edited_copy(base: Image.Image, box=EDIT_BOX) -> Image.Image:
    """Base with one clearly retouched region (colour shift + smoothing)."""
    im = base.copy()
    region = im.crop(box).filter(ImageFilter.GaussianBlur(3))
    arr = np.asarray(region).astype(np.int16)
    arr[..., 0] = np.clip(arr[..., 0] + 60, 0, 255)
    arr[..., 2] = np.clip(arr[..., 2] - 40, 0, 255)
    im.paste(Image.fromarray(arr.astype(np.uint8), "RGB"), box[:2])
    return im


def to_bytes(im: Image.Image, fmt: str = "JPEG", **kwargs) -> bytes:
    buf = io.BytesIO()
    im.save(buf, fmt, **kwargs)
    return buf.getvalue()


def lowres_jpeg_export(edited: Image.Image, factor: float = 0.5, quality: int = 75) -> bytes:
    w, h = edited.size
    small = edited.resize((round(w * factor), round(h * factor)), Image.Resampling.BILINEAR)
    return to_bytes(small, "JPEG", quality=quality)

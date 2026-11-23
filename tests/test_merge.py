"""Smart Merge image-processing tests on small synthetic fixtures."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from pixelrelay import images, merge
from tests.fixtures import EDIT_BOX, edited_copy, lowres_jpeg_export, textured_photo, to_bytes


def load(data: bytes) -> Image.Image:
    return images.load_rgb(data)[0]


@pytest.fixture(scope="module")
def base():
    return textured_photo()


def mask_stats(mask: Image.Image, base_size, box=EDIT_BOX):
    m = np.asarray(mask.resize(base_size, Image.Resampling.BILINEAR), dtype=np.float32) / 255
    x0, y0, x1, y1 = box
    inside = m[y0 + 10 : y1 - 10, x0 + 10 : x1 - 10].mean()
    outside = m.copy()
    outside[max(0, y0 - 40) : y1 + 40, max(0, x0 - 40) : x1 + 40] = 0
    return inside, float((outside > 0.5).mean())


def test_lowres_jpeg_export_produces_local_mask(base):
    edit = load(lowres_jpeg_export(edited_copy(base)))
    al = merge.align(base, edit)
    assert not al.blocked
    assert al.correlation > 0.9
    assert any(w.code == "lower_resolution" for w in al.warnings)
    res = merge.difference_mask(base, edit, al, "medium")
    inside, outside_fp = mask_stats(res.mask, base.size)
    assert inside > 0.9, "the retouched region should be selected"
    assert outside_fp < 0.01, "compression/resize noise should not be selected"


def test_composite_keeps_canvas_and_untouched_pixels(base):
    edit = load(lowres_jpeg_export(edited_copy(base)))
    al = merge.align(base, edit)
    res = merge.difference_mask(base, edit, al, "medium")
    out = merge.composite(base, edit, al.placement, res.mask)
    assert out.size == base.size
    full_mask = np.asarray(res.mask.resize(base.size, Image.Resampling.BILINEAR))
    untouched = full_mask == 0
    assert untouched.mean() > 0.8
    a, b = np.asarray(out), np.asarray(base)
    assert np.array_equal(a[untouched], b[untouched]), "untouched regions must be the original pixels"
    x0, y0, x1, y1 = EDIT_BOX
    # Inside the edit the result follows the contributor's colour change.
    assert a[y0 + 20 : y1 - 20, x0 + 20 : x1 - 20, 0].mean() > b[y0 + 20 : y1 - 20, x0 + 20 : x1 - 20, 0].mean() + 30


def test_sensitivity_is_monotonic(base):
    edit = load(lowres_jpeg_export(edited_copy(base)))
    al = merge.align(base, edit)
    fr = {s: merge.difference_mask(base, edit, al, s).changed_fraction for s in ("low", "medium", "high")}
    assert fr["low"] <= fr["medium"] <= fr["high"]


def test_identical_export_finds_nothing(base):
    edit = load(lowres_jpeg_export(base, factor=0.6, quality=80))
    al = merge.align(base, edit)
    res = merge.difference_mask(base, edit, al, "medium")
    assert res.changed_fraction < 0.005


def test_rotated_export_is_turned_back(base):
    edited = edited_copy(base).transpose(Image.Transpose.ROTATE_270)
    edit = load(to_bytes(edited.resize((300, 450)), "JPEG", quality=85))
    al = merge.align(base, edit)
    assert not al.blocked
    assert al.placement.rotate in (1, 3)
    assert any(w.code == "rotated" for w in al.warnings)
    res = merge.difference_mask(base, edit, al, "medium")
    inside, outside_fp = mask_stats(res.mask, base.size)
    assert inside > 0.85 and outside_fp < 0.02


def test_cropped_export_is_located_and_warned(base):
    edited = edited_copy(base)
    crop = edited.crop((150, 60, 750, 500))  # 600x440, different aspect ratio
    edit = load(to_bytes(crop.resize((450, 330)), "JPEG", quality=85))
    al = merge.align(base, edit)
    assert not al.blocked
    assert al.placement.method == "crop-search"
    assert any(w.code == "cropped" for w in al.warnings)
    assert abs(al.placement.ox - 150) < 6 and abs(al.placement.oy - 60) < 6
    res = merge.difference_mask(base, edit, al, "medium")
    out = merge.composite(base, edit, al.placement, res.mask)
    assert out.size == base.size
    # Outside the cropped edit, the original is kept exactly.
    a, b = np.asarray(out), np.asarray(base)
    assert np.array_equal(a[:50], b[:50])


def test_unrelated_image_is_blocked(base):
    rng = np.random.default_rng(99)
    blotches = Image.fromarray(rng.integers(0, 255, (12, 18, 3), dtype=np.uint8), "RGB")
    other = blotches.resize((450, 300), Image.Resampling.BICUBIC)
    edit = load(to_bytes(other, "JPEG", quality=85))
    al = merge.align(base, edit)
    assert al.blocked
    assert any(w.code == "alignment_unreliable" for w in al.warnings)


def test_reshaped_region_warns_geometry(base):
    edited = base.copy()
    patch = base.crop((520, 300, 700, 460))
    edited.paste(patch, (526, 304))  # a local shift, like a liquify push
    edit = load(to_bytes(edited.resize((675, 450)), "JPEG", quality=90))
    al = merge.align(base, edit)
    assert any(w.code == "geometry_changed" for w in al.warnings)


def test_shifted_export_is_corrected(base):
    edited = edited_copy(base)
    shifted = Image.new("RGB", base.size)
    shifted.paste(edited, (12, -8))
    edit = load(to_bytes(shifted.resize((450, 300)), "JPEG", quality=85))
    al = merge.align(base, edit)
    # Edit pixel (x, y) shows base pixel (x - 12, y + 8): its top-left lands at (-12, 8).
    assert abs(al.placement.ox + 12) <= 3 and abs(al.placement.oy - 8) <= 3
    res = merge.difference_mask(base, edit, al, "medium")
    inside, outside_fp = mask_stats(res.mask, base.size)
    assert inside > 0.85 and outside_fp < 0.02


def test_manual_alignment_overrides(base):
    edit = load(lowres_jpeg_export(edited_copy(base)))
    al = merge.align(base, edit, manual={"rotate": 0, "scale": 1.0, "dx": 0.0, "dy": 0.0})
    assert al.placement.method == "manual"
    assert al.placement.sx == pytest.approx(2.0)


def test_exif_orientation_is_normalised(base):
    data = to_bytes(base, "JPEG", quality=92, exif=_exif_orientation(6))
    info = images.probe(data, 50)
    assert (info.width, info.height) == (600, 900)
    im, _, exif = images.load_rgb(data)
    assert im.size == (600, 900)
    assert exif.get(0x0112) == 1


def _exif_orientation(value: int) -> bytes:
    ex = Image.Exif()
    ex[0x0112] = value
    return ex.tobytes()


def test_probe_rejects_non_images():
    with pytest.raises(images.ImageRejected):
        images.probe(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", 50)
    with pytest.raises(images.ImageRejected):
        images.probe(b"GIF89a" + b"\0" * 100, 50)


def test_probe_rejects_oversized():
    im = Image.new("RGB", (3000, 2000))
    with pytest.raises(images.ImageRejected):
        images.probe(to_bytes(im, "PNG"), 5)


def test_ncc_matches_known_offset():
    rng = np.random.default_rng(1)
    img = rng.random((80, 100)).astype(np.float32)
    tmpl = img[20:50, 30:70]
    y, x, s = merge._peak(merge.ncc_map(img, tmpl))
    assert (y, x) == (20, 30) and s > 0.999


def test_mask_png_roundtrip_is_lossless():
    arr = np.arange(256, dtype=np.uint8)[None, :].repeat(10, 0)
    data = images.encode_png(Image.fromarray(arr, "L"))
    back = np.asarray(Image.open(io.BytesIO(data)))
    assert np.array_equal(arr, back)

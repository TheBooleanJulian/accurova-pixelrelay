"""Smart Merge: align a contributor's export to the original, find what changed,
and composite the changed regions onto the full-resolution original.

Pure numpy + Pillow; no OpenCV. Every function takes and returns plain values
so it can be tested with small synthetic images.

Conventions
-----------
* B = original (base), E = contributor edit, M = mask in [0, 1], F = result.
* F = M * E + (1 - M) * B on the original's pixel grid.
* Mask PNGs are 8-bit greyscale at "work size" (long edge <= 2048):
  white = use the edit, black = keep the original, grey = blend.
* A placement maps edit pixels into base pixels: x_base = sx * x_edit + ox.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from PIL import Image, ImageChops

from .images import fit_size, work_size

# ---------------------------------------------------------------------------
# Parameters

SENSITIVITY = {
    # delta_e: perceptual colour difference (CIE76) that counts as a change.
    # dssim: structural dissimilarity (1 - SSIM) / 2 that counts as a change.
    "low": {"delta_e": 12.0, "dssim": 0.20},
    "medium": {"delta_e": 7.0, "dssim": 0.12},
    "high": {"delta_e": 4.0, "dssim": 0.07},
}

ASPECT_TOLERANCE = 0.02  # relative aspect difference still treated as "same framing"
CORRELATION_BLOCK = 0.45  # below this the edit is not treated as the same picture
CORRELATION_WARN = 0.75
MOVED_TILE_PX = 2.0  # local shift (at 1024 px) that counts as moved/reshaped
DETECT_EDGE = 1024  # long edge of the grid the difference detector runs on


@dataclass
class MergeWarning:
    code: str
    level: str  # info | warning | blocker
    message: str


@dataclass
class Placement:
    rotate: int = 0  # quarter turns counter-clockwise applied to the edit first
    sx: float = 1.0  # base px per edit px
    sy: float = 1.0
    ox: float = 0.0  # base px where the edit's top-left corner lands
    oy: float = 0.0
    method: str = "scale"  # scale | crop-search | manual


@dataclass
class Alignment:
    placement: Placement
    correlation: float
    coverage: float
    edit_size: tuple[int, int]  # after rotation
    base_size: tuple[int, int]
    moved_tiles: int = 0
    textured_tiles: int = 0
    warnings: list[MergeWarning] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(w.level == "blocker" for w in self.warnings)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["blocked"] = self.blocked
        return d


def placement_from_dict(d: dict) -> Placement:
    p = d.get("placement", d)
    return Placement(
        rotate=int(p.get("rotate", 0)) % 4,
        sx=float(p["sx"]),
        sy=float(p["sy"]),
        ox=float(p["ox"]),
        oy=float(p["oy"]),
        method=str(p.get("method", "scale")),
    )


# ---------------------------------------------------------------------------
# Small numpy image operations


def _to_float(im: Image.Image) -> np.ndarray:
    return np.asarray(im, dtype=np.float32) / 255.0


def _gray(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _box_1d(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    if r <= 0:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    return _box_1d(_box_1d(a, r, 0), r, 1)


def soft_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Three box passes: a cheap Gaussian approximation."""
    for _ in range(3):
        a = box_blur(a, r)
    return a


def _rank_1d(a: np.ndarray, r: int, axis: int, op) -> np.ndarray:
    if r <= 0:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    p = np.pad(a, pad, mode="edge")
    n = a.shape[axis]
    out = np.take(p, np.arange(0, n), axis=axis)
    for k in range(1, 2 * r + 1):
        out = op(out, np.take(p, np.arange(k, k + n), axis=axis))
    return out


def max_filter(a: np.ndarray, r: int) -> np.ndarray:
    return _rank_1d(_rank_1d(a, r, 0, np.maximum), r, 1, np.maximum)


def min_filter(a: np.ndarray, r: int) -> np.ndarray:
    return _rank_1d(_rank_1d(a, r, 0, np.minimum), r, 1, np.minimum)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array(
        [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]],
        dtype=np.float32,
    )
    xyz = lin @ m.T
    xyz /= np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    eps = 216 / 24389
    kappa = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    lab = np.empty_like(xyz)
    lab[..., 0] = 116 * f[..., 1] - 16
    lab[..., 1] = 500 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200 * (f[..., 1] - f[..., 2])
    return lab


def ssim_map(x: np.ndarray, y: np.ndarray, r: int = 3) -> np.ndarray:
    """Local SSIM of two greyscale images in [0, 1] (box window of 2r+1)."""
    c1, c2 = 0.01**2, 0.03**2
    mx, my = box_blur(x, r), box_blur(y, r)
    vx = box_blur(x * x, r) - mx * mx
    vy = box_blur(y * y, r) - my * my
    cxy = box_blur(x * y, r) - mx * my
    num = (2 * mx * my + c1) * (2 * cxy + c2)
    den = (mx * mx + my * my + c1) * (vx + vy + c2)
    return np.clip(num / den, -1.0, 1.0)


def ncc_map(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of `template` over every valid position of `image`.

    Equivalent to OpenCV's TM_CCOEFF_NORMED; result shape is
    (H - h + 1, W - w + 1). Flat windows score 0.
    """
    H, W = image.shape
    h, w = template.shape
    if h > H or w > W:
        raise ValueError("template larger than image")
    t = template.astype(np.float64) - template.mean()
    t_energy = float((t * t).sum())
    img = image.astype(np.float64)
    fh, fw = H + h, W + w
    corr = np.fft.irfft2(np.fft.rfft2(img, (fh, fw)) * np.conj(np.fft.rfft2(t, (fh, fw))), (fh, fw))[
        : H - h + 1, : W - w + 1
    ]

    def window_sum(a: np.ndarray) -> np.ndarray:
        s = np.pad(a, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return s[h:, w:] - s[:-h, w:] - s[h:, :-w] + s[:-h, :-w]

    n = h * w
    s1 = window_sum(img)
    s2 = window_sum(img * img)
    var = np.maximum(s2 - s1 * s1 / n, 0.0)
    den = np.sqrt(var * t_energy)
    out = np.zeros_like(corr)
    ok = den > 1e-9
    out[ok] = corr[ok] / den[ok]
    return np.clip(out, -1.0, 1.0)


def _peak(score: np.ndarray) -> tuple[int, int, float]:
    idx = int(np.argmax(score))
    y, x = divmod(idx, score.shape[1])
    return y, x, float(score[y, x])


def _pearson(a: np.ndarray, b: np.ndarray, valid: np.ndarray | None = None) -> float:
    if valid is not None:
        a, b = a[valid], b[valid]
    a = a.astype(np.float64).ravel() - a.mean()
    b = b.astype(np.float64).ravel() - b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 1e-12 else 0.0


# ---------------------------------------------------------------------------
# Placement (alignment)


def rotate_edit(edit: Image.Image, quarters: int) -> Image.Image:
    quarters %= 4
    if quarters == 0:
        return edit
    return edit.transpose(
        {1: Image.Transpose.ROTATE_90, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_270}[quarters]
    )


def place(
    edit: Image.Image, p: Placement, canvas: tuple[int, int], base_size: tuple[int, int]
) -> tuple[Image.Image, Image.Image]:
    """Render the (rotated) edit onto a canvas that is the base scaled to `canvas`.

    Returns (RGB image, L coverage mask: 255 where the edit has pixels).
    """
    f = canvas[0] / base_size[0]
    e = rotate_edit(edit, p.rotate)
    ew, eh = e.size
    tw = max(1, round(ew * p.sx * f))
    th = max(1, round(eh * p.sy * f))
    ox, oy = round(p.ox * f), round(p.oy * f)
    if (tw, th) == canvas and (ox, oy) == (0, 0):
        resized = e if e.size == canvas else e.resize(canvas, Image.Resampling.LANCZOS)
        return resized, Image.new("L", canvas, 255)
    resized = e.resize((tw, th), Image.Resampling.LANCZOS)
    out = Image.new("RGB", canvas, (0, 0, 0))
    out.paste(resized, (ox, oy))
    cov = Image.new("L", canvas, 0)
    cov.paste(255, (ox, oy, ox + tw, oy + th))
    return out, cov


def _small_gray(im: Image.Image, size: tuple[int, int], blur: int = 1) -> np.ndarray:
    g = _gray(
        _to_float(
            im.convert("RGB").resize(
                size, Image.Resampling.BOX if min(size) < min(im.size) else Image.Resampling.BICUBIC
            )
        )
    )
    return box_blur(g, blur) if blur else g


def _search_crop(base: Image.Image, edit: Image.Image) -> tuple[Placement, float]:
    """Find where a (possibly cropped and rescaled) edit sits inside the base."""
    Wb, Hb = base.size
    We, He = edit.size

    def level(long_edge: int, fractions) -> tuple[float, int, int, float, tuple[int, int]]:
        bw, bh = (
            (long_edge, max(8, round(long_edge * Hb / Wb)))
            if Wb >= Hb
            else (max(8, round(long_edge * Wb / Hb)), long_edge)
        )
        bimg = _small_gray(base, (bw, bh))
        best = (-2.0, 0, 0, 1.0, (0, 0))
        for frac in fractions:
            tw = round(frac * bw)
            th = round(tw * He / We)
            if tw < 16 or th < 16 or tw > bw or th > bh:
                continue
            tmpl = _small_gray(edit, (tw, th))
            y, x, s = _peak(ncc_map(bimg, tmpl))
            if s > best[0]:
                best = (s, x, y, frac, (bw, bh))
        return best

    f_max = min(1.0, (Hb * We) / (He * Wb))
    coarse = level(384, np.linspace(f_max, 0.4 * f_max, 25))
    _, _, _, frac, _ = coarse
    fine = level(768, np.linspace(frac * 0.97, min(f_max, frac * 1.03), 11))
    score, x, y, frac, (bw, _bh) = fine
    scale = frac * Wb / We
    return Placement(0, scale, scale, x * Wb / bw, y * Wb / bw, "crop-search"), score


def _refine_shift(base: Image.Image, edit: Image.Image, p: Placement) -> tuple[Placement, float]:
    """Small translation correction for full-frame edits (within +/-4%)."""
    Wb, Hb = base.size
    lw, lh = (512, max(8, round(512 * Hb / Wb))) if Wb >= Hb else (max(8, round(512 * Wb / Hb)), 512)
    b = _small_gray(base, (lw, lh))
    placed, _ = place(edit, p, (lw, lh), base.size)
    e = box_blur(_gray(_to_float(placed)), 1)
    mx, my = max(2, round(lw * 0.04)), max(2, round(lh * 0.04))
    tmpl = e[my : lh - my, mx : lw - mx]
    y, x, score = _peak(ncc_map(b, tmpl))
    dx, dy = x - mx, y - my
    if (dx or dy) and score > 0.5:
        p = Placement(p.rotate, p.sx, p.sy, p.ox + dx * Wb / lw, p.oy + dy * Hb / lh, p.method)
    return p, score


def _moved_tiles(base: Image.Image, edit: Image.Image, p: Placement) -> tuple[int, int]:
    """Count textured tiles whose content is locally shifted (liquify, reshaping)."""
    Wb, Hb = base.size
    lw, lh = (1024, max(8, round(1024 * Hb / Wb))) if Wb >= Hb else (max(8, round(1024 * Wb / Hb)), 1024)
    if lw < 128 or lh < 128:
        return 0, 0
    b = _small_gray(base, (lw, lh), blur=0)
    placed, cov = place(edit, p, (lw, lh), base.size)
    e = _gray(_to_float(placed))
    covered = np.asarray(cov) > 0
    cols, rows, search = 6, 4, 8
    tw, th = lw // cols, lh // rows
    moved = textured = 0
    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * tw, r * th
            inner = e[y0 + search : y0 + th - search, x0 + search : x0 + tw - search]
            if inner.size == 0 or not covered[y0 : y0 + th, x0 : x0 + tw].all():
                continue
            if inner.std() < 0.04:
                continue  # flat tile: no reliable signal
            textured += 1
            region = b[y0 : y0 + th, x0 : x0 + tw]
            y, x, score = _peak(ncc_map(region, inner))
            if score < 0.6:
                continue  # changed so much that position is meaningless; the mask covers it
            if np.hypot(x - search, y - search) >= MOVED_TILE_PX:
                moved += 1
    return moved, textured


def align(base: Image.Image, edit: Image.Image, manual: dict | None = None) -> Alignment:
    """Work out how the edit maps onto the base and how trustworthy that is.

    `base` and `edit` are oriented RGB images (EXIF already applied), the edit
    already in the base's colour profile. `manual`, when given, is a dict with
    rotate (quarter turns), scale (multiplier, 1 = fit to width) and dx, dy
    (fractions of the base width/height) and replaces automatic placement.
    """
    Wb, Hb = base.size
    warnings: list[MergeWarning] = []
    base_portrait = Hb > Wb

    if manual:
        q = int(manual.get("rotate", 0)) % 4
        er = rotate_edit(edit, q)
        fit = Wb / er.size[0]
        s = fit * float(manual.get("scale", 1.0))
        p = Placement(q, s, s, float(manual.get("dx", 0.0)) * Wb, float(manual.get("dy", 0.0)) * Hb, "manual")
        candidates = [(p, 1.0)]
    else:
        candidates = []
        for q in (0, 2, 1, 3):
            er = rotate_edit(edit, q)
            We, He = er.size
            if (He > We) != base_portrait and abs(We - He) > 0.02 * max(We, He):
                continue  # wrong orientation class for this rotation
            aspect_diff = abs((We / He) - (Wb / Hb)) / (Wb / Hb)
            if aspect_diff <= ASPECT_TOLERANCE:
                p = Placement(q, Wb / We, Hb / He, 0.0, 0.0, "scale")
                p, score = _refine_shift(base, edit, p)
            else:
                p, score = _search_crop(base, er)
                p.rotate = q
            candidates.append((p, score))
            if q == 0 and score > 0.9:
                break  # clearly upright; skip the other rotations
        if not candidates:  # square-ish mismatch: fall back to a plain fit
            p = Placement(0, Wb / edit.size[0], Hb / edit.size[1], 0, 0, "scale")
            candidates = [(p, 0.0)]

    best_p, best_corr = None, -2.0
    lw, lh = work_size(Wb, Hb)
    small = (min(512, lw), max(8, round(min(512, lw) * Hb / Wb)))
    b_small = _small_gray(base, small, blur=1)
    for p, _ in candidates:
        placed, cov = place(edit, p, small, base.size)
        valid = np.asarray(cov) > 0
        if valid.mean() < 0.05:
            continue
        corr = _pearson(b_small, box_blur(_gray(_to_float(placed)), 1), valid)
        if corr > best_corr:
            best_p, best_corr = p, corr
    if best_p is None:
        best_p, best_corr = candidates[0][0], 0.0

    placed, cov = place(edit, best_p, small, base.size)
    coverage = float((np.asarray(cov) > 0).mean())
    er_size = rotate_edit(edit, best_p.rotate).size

    if best_p.rotate:
        warnings.append(
            MergeWarning(
                "rotated",
                "info",
                f"Your edit was rotated {best_p.rotate * 90} degrees compared with the original, so it was turned back.",
            )
        )
    if best_p.method == "crop-search":
        warnings.append(
            MergeWarning(
                "cropped",
                "warning",
                "Your edit looks cropped or reframed. It was lined up automatically; "
                "areas outside your edit keep the original. Check the preview.",
            )
        )
    if best_corr < CORRELATION_BLOCK:
        warnings.append(
            MergeWarning(
                "alignment_unreliable",
                "blocker",
                "This edit could not be lined up with the original. Check that it is the right photo, "
                "or line it up by hand, or upload an export without cropping or rotation.",
            )
        )
    elif best_corr < CORRELATION_WARN:
        warnings.append(
            MergeWarning(
                "alignment_uncertain",
                "warning",
                "The edit only partly matches the original, so the line-up may be off. Check the preview closely.",
            )
        )

    upscale = max(best_p.sx, best_p.sy)
    if upscale > 1.1:
        warnings.append(
            MergeWarning(
                "lower_resolution",
                "info",
                f"Your edit is {er_size[0]}x{er_size[1]}; the original is {Wb}x{Hb}. Untouched areas keep the "
                "original's full detail, but the areas you changed come from your smaller file and will be softer.",
            )
        )

    moved, textured = (0, 0)
    if best_corr >= CORRELATION_BLOCK:
        moved, textured = _moved_tiles(base, edit, best_p)
        if moved:
            warnings.append(
                MergeWarning(
                    "geometry_changed",
                    "warning",
                    f"Parts of the picture look moved or reshaped ({moved} of {textured} areas checked). "
                    "Moved areas may look doubled after merging; paint them fully or check the preview.",
                )
            )

    return Alignment(best_p, round(best_corr, 4), round(coverage, 4), er_size, (Wb, Hb), moved, textured, warnings)


# ---------------------------------------------------------------------------
# Difference mask


@dataclass
class MaskResult:
    mask: Image.Image  # L, work size
    changed_fraction: float
    warnings: list[MergeWarning]
    settings: dict


def difference_mask(
    base: Image.Image, edit: Image.Image, alignment: Alignment, sensitivity: str = "medium"
) -> MaskResult:
    """Build the Smart Merge mask at work size."""
    if sensitivity not in SENSITIVITY:
        raise ValueError("unknown sensitivity")
    params = SENSITIVITY[sensitivity]
    Wb, Hb = base.size
    ws = work_size(Wb, Hb)  # size of the stored mask
    ds = fit_size(Wb, Hb, DETECT_EDGE)  # detection runs on a smaller grid
    p = alignment.placement

    e_img, cov_img = place(edit, p, ds, base.size)
    covered = np.asarray(cov_img, dtype=np.float32) / 255.0

    # How much the edit is enlarged on the work grid. When the export is
    # smaller than the work grid, compare it against the original put through
    # the same downscale and upscale, so resampling softness cancels out.
    enlarge = max(p.sx, p.sy) * ds[0] / Wb
    if enlarge > 1.05:
        er_size = rotate_edit(edit, p.rotate).size
        box = (p.ox, p.oy, p.ox + er_size[0] * p.sx, p.oy + er_size[1] * p.sy)
        # crop() pads with black outside the base; coverage masks that out later.
        degraded = base.crop(tuple(round(v) for v in box)).resize(er_size, Image.Resampling.LANCZOS)
        b_img, _ = place(degraded, Placement(0, p.sx, p.sy, p.ox, p.oy), ds, base.size)
    else:
        b_img = base if base.size == ds else base.resize(ds, Image.Resampling.LANCZOS)
    b = _to_float(b_img)
    e = _to_float(e_img)

    # Brightness is compared at a fine scale; colour at a coarser one, since
    # JPEG stores colour at half resolution (4:2:0) and smears it.
    unit = max(1, round(max(ds) / 1024))
    scale = max(1.0, enlarge)
    r_luma = max(1, round(0.5 * scale)) * unit
    r_chroma = max(2, round(2 * scale)) * unit
    lab_fine_b, lab_fine_e = srgb_to_lab(soft_blur(b, r_luma)), srgb_to_lab(soft_blur(e, r_luma))
    lab_c_b, lab_c_e = srgb_to_lab(soft_blur(b, r_chroma)), srgb_to_lab(soft_blur(e, r_chroma))
    d_luma = lab_fine_b[..., 0] - lab_fine_e[..., 0]
    d_chroma = np.linalg.norm(lab_c_b[..., 1:] - lab_c_e[..., 1:], axis=-1)
    delta_e = np.sqrt(d_luma * d_luma + d_chroma * d_chroma)
    dssim = (1.0 - ssim_map(lab_fine_b[..., 0] / 100.0, lab_fine_e[..., 0] / 100.0, r=3 * unit)) / 2.0
    score = np.maximum(delta_e / params["delta_e"], dssim / params["dssim"])
    changed = (score >= 1.0).astype(np.float32)

    # Ignore a thin band at the edge of the edit's coverage (resampling edges).
    inside = min_filter(covered, 2 * unit)
    changed *= inside

    # Morphology: drop specks, close gaps, grow slightly, then feather.
    changed = max_filter(min_filter(changed, unit), unit)  # open
    changed = min_filter(max_filter(changed, 4 * unit), 4 * unit)  # close
    changed = max_filter(changed, 2 * unit)  # dilate
    soft = soft_blur(changed, 2 * unit)
    soft = np.clip(soft * inside, 0.0, 1.0)
    soft[soft < 1.5 / 255] = 0.0  # keep untouched areas exactly 0

    mask = Image.fromarray(np.round(soft * 255).astype(np.uint8), "L")
    if mask.size != ws:
        mask = mask.resize(ws, Image.Resampling.BILINEAR)
    fraction = float((soft > 0.5).mean())
    warnings = []
    if fraction > 0.7:
        warnings.append(
            MergeWarning(
                "most_changed",
                "info",
                "Most of the picture changed (for example a colour grade). That is fine if intended; "
                "the whole edit will be used.",
            )
        )
    elif fraction == 0.0:
        warnings.append(
            MergeWarning(
                "nothing_found",
                "warning",
                "No changes were found. Try High sensitivity, or paint the areas you edited.",
            )
        )
    settings = {
        "sensitivity": sensitivity,
        **params,
        "luma_radius": r_luma,
        "chroma_radius": r_chroma,
        "work_size": list(ws),
        "detect_size": list(ds),
    }
    return MaskResult(mask, round(fraction, 4), warnings, settings)


# ---------------------------------------------------------------------------
# Compositing


def composite(base: Image.Image, edit: Image.Image, placement: Placement, mask: Image.Image) -> Image.Image:
    """F = M*E + (1-M)*B at the base's full resolution.

    `mask` may be any size with the base's aspect ratio (normally work size);
    it is upsampled smoothly. Where the mask is 0 the result is the base's
    pixel values exactly.
    """
    size = base.size
    placed, cov = place(edit, placement, size, size)
    m = mask.convert("L")
    if m.size != size:
        m = m.resize(size, Image.Resampling.BILINEAR)
    m = ImageChops.multiply(m, cov)
    return Image.composite(placed, base, m)


def preview_composite(base_preview: Image.Image, edit_preview: Image.Image, mask: Image.Image) -> Image.Image:
    """Work-size composite of images that are already on the same grid."""
    m = mask.convert("L")
    if m.size != base_preview.size:
        m = m.resize(base_preview.size, Image.Resampling.BILINEAR)
    return Image.composite(edit_preview, base_preview, m)

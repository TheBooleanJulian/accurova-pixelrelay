# Image processing (Smart Merge)

Code: `pixelrelay/merge.py`, `pixelrelay/images.py`, job handlers in `pixelrelay/jobs.py`. Tests: `tests/test_merge.py`.

## Inputs and normalisation

- **Decode** with Pillow; accept JPEG, PNG, WebP and TIFF. **EXIF orientation** is applied and the stored orientation reset to 1.
- **Colour:** the original's pixel values are never converted, so untouched pixels stay exact. The edit is converted into the original's ICC profile (sRGB when either has none). Previews for the browser are converted to sRGB.
- Modes other than RGB (CMYK, 16-bit, palette, alpha) are converted to 8-bit RGB, with a note to the contributor.

## 1. Alignment (`merge.align`)

The goal: a placement `x_base = s * x_edit + offset`, after a possible quarter-turn rotation.

1. **Orientation candidates:** 0° and 180° when the edit has the same orientation as the original; 90° and 270° when it doesn't (portrait vs landscape).
2. **Same framing** (aspect ratios within 2%): scale to fit, then a translation check. Normalised cross-correlation (NCC, FFT-based) of the central 92% of the edit over the original at 512 px finds shifts of up to ±4%.
3. **Different framing** (likely cropped): a two-level search over scales. 25 scales at 384 px, then 11 finer scales at 768 px. Each runs a full NCC template match. Best peak wins.
4. **Pick** the candidate with the highest correlation (Pearson, on blurred greyscale in the overlap).
5. **Checks and warnings** (`info` / `warning` / `blocker`):
   - `rotated`: info, turned back automatically.
   - `cropped`: warning; areas outside the edit keep the original.
   - `alignment_uncertain`: warning, correlation below 0.75.
   - `alignment_unreliable`: **blocker**, correlation below 0.45. Submission is refused until the contributor lines it up by hand, or uploads a better export. After a manual line-up it becomes a warning, because they can see the result.
   - `lower_resolution`: info, the edit is smaller, so edited areas will be softer. Never claimed as restored detail.
   - `geometry_changed`: warning. A 6x4 grid of textured tiles at 1024 px is checked for local shifts of 2 px or more, which catches liquify, reshaping and moved parts that a mask can't transfer cleanly.

**Manual line-up:** the editor shows the actual upload at half opacity over the original. The contributor rotates, scales and drags it, and the server re-runs the analysis with that placement.

## 2. Difference mask (`merge.difference_mask`)

Runs on a 1024 px grid. The result is stored at work size (≤ 2048 px).

1. **Scale-aware reference:** when the export is smaller than the detection grid, the original is put through the same downscale and upscale, so resampling softness cancels out.
2. **Two-scale colour difference (CIE76 ΔE in Lab):** lightness is compared after a light blur. Colour (a*, b*) is compared after a wider blur, because JPEG stores colour at half resolution (4:2:0). This was the main source of false positives in testing.
3. **Structural difference:** local SSIM on lightness, `dssim = (1 - SSIM) / 2`.
4. `score = max(ΔE / t_ΔE, dssim / t_ssim)`; changed where `score ≥ 1`.

   | Sensitivity | t_ΔE | t_ssim |
   |---|---|---|
   | Low (obvious only) | 12 | 0.20 |
   | **Medium** (default) | 7 | 0.12 |
   | High (subtle) | 4 | 0.07 |

5. **Clean-up:** a thin band at the edit's edges is ignored; then opening (drop specks), closing (fill gaps), a small dilation, and a feather. Values below 1.5/255 are set to exactly 0, so untouched areas stay untouched.
6. White = use the edit, black = keep the original, grey = blend.

Detailed thresholds live in `SENSITIVITY` in `merge.py`. Contributors only ever see Low, Medium and High.

## 3. Brush refinement (browser, `editor.js`)

The editor blends `base_preview`, `edit_preview` and the mask on a canvas at work size:

- **Add edit** (brush) and **Keep original** (eraser), with size and softness. Pen pressure is supported.
- Undo and redo (dirty-rectangle history), invert, clear, use whole edit.
- Result / Original / Your edit views, a magenta highlight with adjustable strength, and hold `\` to peek at the original.
- Zoom and pan: wheel, buttons, two-finger pinch, the Move tool, or Space-drag.

Within one stroke, coverage takes the maximum per pixel, so overlapping dabs don't build up hard edges. Saving uploads the mask as a PNG. The server stores it as a new mask; nothing is overwritten.

## 4. Full-resolution composite (`merge.composite`)

`F = M·E + (1 - M)·B` at the original's size, via Pillow `Image.composite`:

- B is the original as decoded (oriented).
- E is the edit, LANCZOS-resampled and placed with the stored placement.
- M is the mask, bilinear-upsampled and multiplied by the edit's coverage.

Where `M = 0`, output pixels equal the original's exactly (tested). The result is saved as a **lossless PNG** with the original's ICC profile and EXIF, plus a work-size JPEG preview. The export converts to JPEG quality 95 on request.

## Performance (24 MP original, 0.35x JPEG export; dev laptop)

| Step | Time |
|---|---|
| Alignment | 0.5 s |
| Mask | 2.1 s |
| Composite | 0.4 s |
| PNG encode | 1.2 s (34 MB) |

Decoding is extra. All of it runs in the background worker.

## Limitations

- Upscaling a small export cannot restore detail. Edited areas are as sharp as the export.
- Reshaping (liquify), moved limbs and major geometry changes are detected and warned about, not corrected. There is no warping.
- Alignment handles uniform scale, translation, quarter-turn rotation and crops. Arbitrary rotation, perspective and mirroring are not detected; those exports end up `blocked` and need a manual line-up or a better export.
- Strong global colour grades select most of the picture. That is intended (a note says so) but leaves less to refine.
- HEIC/HEIF uploads are not supported (would need a plugin). Ask for JPEG or PNG exports.

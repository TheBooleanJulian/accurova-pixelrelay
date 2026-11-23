"""Write synthetic originals and an edited low-res export for the browser test.

python -m tests.e2e.make_fixtures <out_dir>
"""

import sys
from pathlib import Path

from tests.fixtures import edited_copy, lowres_jpeg_export, textured_photo, to_bytes


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        im = textured_photo(2400, 1600, seed=11 + i)
        (out / f"original_{i + 1}.jpg").write_bytes(to_bytes(im, "JPEG", quality=94))
        if i == 0:
            edited = edited_copy(im, (800, 480, 1300, 900))
            (out / "edit_1.jpg").write_bytes(lowres_jpeg_export(edited, factor=0.5, quality=82))


if __name__ == "__main__":
    main(Path(sys.argv[1]))

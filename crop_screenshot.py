"""
Crop a headless-Chrome screenshot to its content (page background #f5f5f5).

    python crop_screenshot.py <raw.png> <out.png>
"""
import sys

import numpy as np
from PIL import Image

BG = np.array([245, 245, 245])
PAD = 20


def crop(raw_path: str, out_path: str) -> tuple[int, int] | None:
    img = Image.open(raw_path).convert("RGB")
    mask = np.any(np.array(img) != BG, axis=2)
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if not (len(rows) and len(cols)):
        return None
    box = (max(0, cols[0] - PAD), max(0, rows[0] - PAD),
           min(img.width, cols[-1] + PAD), min(img.height, rows[-1] + PAD))
    cropped = img.crop(box)
    cropped.save(out_path)
    return cropped.size


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    size = crop(sys.argv[1], sys.argv[2])
    print(f"Saved: {size}" if size else "No content found")

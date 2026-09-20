from PIL import Image
import numpy as np

img = Image.open("docs/screenshot-raw.png").convert("RGB")
arr = np.array(img)
bg = np.array([245, 245, 245])
mask = np.any(arr != bg, axis=2)
rows = np.where(mask.any(axis=1))[0]
cols = np.where(mask.any(axis=0))[0]
if len(rows) and len(cols):
    top, bottom = rows[0], rows[-1]
    left, right = cols[0], cols[-1]
    pad = 20
    cropped = img.crop((max(0,left-pad), max(0,top-pad), min(img.width,right+pad), min(img.height,bottom+pad)))
    cropped.save("docs/screenshot-daily.png")
    print(f"Saved: {cropped.size}")
else:
    print("No content found")

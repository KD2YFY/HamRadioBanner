"""Write the app icon (drawn by banner.draw_icon) as icon files for the app builds.

    python tools/make_icons.py OUTPUT_FOLDER

Creates icon.ico (Windows, several sizes), icon.icns (macOS) and icon.png.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # works on build machines with no screen
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PIL import Image  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402

import banner  # noqa: E402


def to_pil(size):
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    banner.draw_icon(size).save(buf, "PNG")
    from io import BytesIO
    return Image.open(BytesIO(bytes(buf.data()))).convert("RGBA")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "icons"
    os.makedirs(out, exist_ok=True)
    app = QGuiApplication(sys.argv)  # noqa: F841 - Qt needs this before drawing
    sizes = [16, 24, 32, 48, 64, 128, 256]
    # Each size is drawn separately (small ones are simplified), not just scaled down.
    images = {s: to_pil(s) for s in sizes + [512, 1024]}
    images[256].save(os.path.join(out, "icon.ico"), sizes=[(s, s) for s in sizes],
                     append_images=[images[s] for s in sizes if s != 256])
    images[1024].save(os.path.join(out, "icon.icns"))
    images[512].save(os.path.join(out, "icon.png"))
    print("wrote", sorted(os.listdir(out)))


if __name__ == "__main__":
    main()

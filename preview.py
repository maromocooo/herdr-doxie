"""スプライトの拡大プレビューを PNG と GIF で書き出す。"""
import argparse
import sys
from PIL import Image, ImageDraw

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sprites import ORDER, grid  # noqa: E402

COLORS = {
    "run1": (249, 226, 175), "run2": (249, 226, 175), "run3": (249, 226, 175),
    "sit": (243, 139, 168), "house": (137, 180, 250), "lie": (166, 227, 161),
    "lie1": (166, 227, 161), "lie0": (166, 227, 161), "house_empty": (108, 112, 134),
}
BG = (30, 30, 46)
S = 12  # 1 ピクセルの拡大率


def draw_sprite(img, name, ox, oy, scale=S, gridlines=True):
    d = ImageDraw.Draw(img)
    g = grid(name)
    for y, row in enumerate(g):
        for x, on in enumerate(row):
            if on:
                d.rectangle([ox + x * scale, oy + y * scale,
                             ox + (x + 1) * scale - 1, oy + (y + 1) * scale - 1],
                            fill=COLORS[name])
            elif gridlines:
                d.point((ox + x * scale, oy + y * scale), fill=(60, 60, 80))
    if gridlines:  # セル境界（8px ごと）
        d.line([ox + 8 * scale, oy, ox + 8 * scale, oy + 16 * scale], fill=(80, 80, 110))


def sheet(path):
    pad = 16
    w = len(ORDER) * (16 * S + pad) + pad
    img = Image.new("RGB", (w, 16 * S + 2 * pad), BG)
    for i, n in enumerate(ORDER):
        draw_sprite(img, n, pad + i * (16 * S + pad), pad)
    img.save(path)


def gif(path):
    frames = []
    for n in ["run1", "run2", "run3", "run2"]:
        img = Image.new("RGB", (16 * S + 32, 16 * S + 32), BG)
        draw_sprite(img, n, 16, 16, gridlines=False)
        frames.append(img)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=250, loop=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="sheet.png")
    parser.add_argument("--gif", action="store_true", help="write a running animation GIF")
    args = parser.parse_args()
    (gif if args.gif else sheet)(args.path)

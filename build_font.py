#!/usr/bin/env python3
"""sprites.py のドット絵から、ターミナル用のアイコンフォント (TTF) を作る。

各スプライト(16x16)を左右 8x16 に分け、私用領域 (Supplementary PUA-B) の
連続した 2 コードポイントに割り当てる。2 文字並べると 1 匹ぶんになる。

    pip install fonttools skia-pathops
    python3 build_font.py                  # HerdrDoxie.ttf を出力
    python3 build_font.py --advance 602    # メインフォントの字幅に合わせる場合
"""
import argparse
import json
import sys
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.removeOverlaps import removeOverlaps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sprites import ORDER, grid  # noqa: E402

BASE_CP = 0x10F000
FAMILY = "Herdr Doxie"
# 絵を変えたら上げる。同じ番号のままだと macOS が古いフォントをキャッシュし続けることがある
FONT_VERSION = "1.040"


def codepoints():
    """スプライト名 -> (左, 右) コードポイント"""
    assert len(ORDER) <= 16, "Dark glyphs must not overlap light aliases at +0x20"
    return {name: (BASE_CP + 2 * i, BASE_CP + 2 * i + 1) for i, name in enumerate(ORDER)}


def half_glyph(g, x0, px, py, bottom, bleed):
    """左右どちらか半分のグリフ。

    左右の境目で両側とも塗られている行は、隣のセル側へ bleed だけはみ出させる。
    字幅（0.6em など）とターミナルの実際のセル幅（整数ピクセル）は一致しないので、
    はみ出しが無いと境目に薄い隙間ができ、アンチエイリアスで縦線に見える。
    """
    pen = TTGlyphPen(None)
    for y, row in enumerate(g):
        for x in range(8):
            if not row[x0 + x]:
                continue
            left, right = x * px, (x + 1) * px
            if x0 == 0 and x == 7 and row[8]:
                right += bleed
            if x0 == 8 and x == 0 and row[7]:
                left -= bleed
            top = bottom + (16 - y) * py
            low = bottom + (15 - y) * py
            # TrueType の外側輪郭は時計回り
            pen.moveTo((left, low))
            pen.lineTo((left, top))
            pen.lineTo((right, top))
            pen.lineTo((right, low))
            pen.closePath()
    return pen.glyph()


def build(out, advance, descent, bleed):
    upm = 1000
    px = advance / 8
    py = px  # 正方形ピクセル（16 段で advance*2 の高さ）
    height = py * 16
    ascent = round(descent + height)
    px, py = round(px), round(py)

    glyph_order = [".notdef", "space"]
    glyphs = {".notdef": TTGlyphPen(None).glyph(), "space": TTGlyphPen(None).glyph()}
    cmap = {0x20: "space"}
    metrics = {".notdef": (advance, 0), "space": (advance, 0)}

    for name, (cl, cr) in codepoints().items():
        g = grid(name)
        for side, cp, x0 in (("L", cl, 0), ("R", cr, 8)):
            gn = f"{name}.{side}"
            glyph_order.append(gn)
            gl = half_glyph(g, x0, px, py, descent, bleed)
            gl.recalcBounds(None)
            glyphs[gn] = gl
            cmap[cp] = gn
            cmap[cp + 0x20] = gn  # Same outline; separate sidebar color rules for light backgrounds.
            # lsb は xMin と一致させる（ずれていると描画時に横へずれる）
            metrics[gn] = (advance, getattr(gl, "xMin", 0))

    fb = FontBuilder(upm, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupNameTable({"copyright": "Copyright (c) 2026 maromocooo",
                       "licenseDescription": "MIT License; see LICENSE in the distribution.",
                       "licenseInfoURL": "https://github.com/maromocooo/herdr-doxie/blob/main/LICENSE",
                       "familyName": FAMILY, "styleName": "Regular",
                       "psName": "HerdrDoxie-Regular",
                       "version": f"Version {FONT_VERSION}",
                       "uniqueFontIdentifier": f"HerdrDoxie-Regular-{FONT_VERSION}"})
    fb.setupOS2(sTypoAscender=ascent, sTypoDescender=descent, sTypoLineGap=0,
                usWinAscent=ascent, usWinDescent=-descent,
                sxHeight=round(height / 2), sCapHeight=ascent,
                achVendID="NONE", fsType=0)
    fb.setupPost(isFixedPitch=1)
    fb.updateHead(fontRevision=float(FONT_VERSION))
    font = fb.font
    removeOverlaps(font)  # ピクセル同士を 1 つの輪郭に結合（継ぎ目を防ぐ）
    font.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="HerdrDoxie.ttf")
    ap.add_argument("--advance", type=int, default=600,
                    help="1 セルの字幅 (units / 1000em)。メインフォントの半角幅に合わせる")
    ap.add_argument("--descent", type=int, default=-250,
                    help="グリフ下端の位置 (units)。上下位置の微調整用")
    ap.add_argument("--bleed", type=int, default=60,
                    help="左右の境目で隣のセルへはみ出させる量 (units)。縦線が残るときは増やす")
    ap.add_argument("--print-map", action="store_true",
                    help="スプライト名とコードポイントの対応を JSON で表示")
    a = ap.parse_args()
    if a.print_map:
        print(json.dumps({k: [f"U+{l:X}", f"U+{r:X}"] for k, (l, r) in codepoints().items()}, indent=2))
        return
    build(a.out, a.advance, a.descent, a.bleed)
    first, last = BASE_CP, BASE_CP + 2 * len(ORDER) - 1
    print(f"wrote {a.out}  ({FAMILY}, U+{first:X}-U+{last:X})")


if __name__ == "__main__":
    main()

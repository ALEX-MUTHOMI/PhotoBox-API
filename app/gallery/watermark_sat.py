"""Summed-area-table watermark corner selection (Phase 3).

Greedy 4-corner quietness via luminance variance. No ML, no backtracking.
"""
from __future__ import annotations

from array import array

from PIL import Image as PILImage


Corner = tuple[int, int]  # top-left of logo box


def _corner_positions(
    width: int,
    height: int,
    logo_w: int,
    logo_h: int,
    margin: int,
) -> dict[str, Corner]:
    br = (
        max(margin, width - logo_w - margin),
        max(margin, height - logo_h - margin),
    )
    return {
        "TL": (margin, margin),
        "TR": (max(margin, width - logo_w - margin), margin),
        "BL": (margin, max(margin, height - logo_h - margin)),
        "BR": br,
    }


def _build_integrals(gray: PILImage.Image) -> tuple[array, array, int, int]:
    """Return (S1, S2, W, H) 1-based prefix sums of L and L^2 as int64 arrays.

    Must use array('q') (signed 64-bit). sumsq for an 800×800 all-white
    canvas is ~4.16e10, which overflows signed 32-bit (~2.14e9) and would
    corrupt variance / corner selection.
    """
    w, h = gray.size
    pixels = gray.load()
    # (h+1) * (w+1) layout, row-major
    stride = w + 1
    s1 = array("q", [0]) * ((h + 1) * stride)
    s2 = array("q", [0]) * ((h + 1) * stride)

    for y in range(1, h + 1):
        row_sum = 0
        row_sumsq = 0
        row_base = y * stride
        prev_base = (y - 1) * stride
        for x in range(1, w + 1):
            lum = int(pixels[x - 1, y - 1])
            row_sum += lum
            row_sumsq += lum * lum
            s1[row_base + x] = s1[prev_base + x] + row_sum
            s2[row_base + x] = s2[prev_base + x] + row_sumsq
    return s1, s2, w, h


def _rect_variance(
    s1: array,
    s2: array,
    stride: int,
    x0: int,
    y0: int,
    box_w: int,
    box_h: int,
) -> float:
    """Variance of inclusive-exclusive pixel box with top-left (x0, y0)."""
    x1 = x0 + box_w
    y1 = y0 + box_h
    n = box_w * box_h
    if n <= 0:
        return float("inf")

    # 1-based SAT: sum over [x0, x1) x [y0, y1) = S(y1,x1)-S(y0,x1)-S(y1,x0)+S(y0,x0)
    def at(s: array, y: int, x: int) -> int:
        return s[y * stride + x]

    total = at(s1, y1, x1) - at(s1, y0, x1) - at(s1, y1, x0) + at(s1, y0, x0)
    total_sq = at(s2, y1, x1) - at(s2, y0, x1) - at(s2, y1, x0) + at(s2, y0, x0)
    mean = total / n
    return (total_sq / n) - (mean * mean)


def choose_quietest_corner(
    canvas: PILImage.Image,
    logo_w: int,
    logo_h: int,
    margin: int,
    *,
    min_pixels: int = 160_000,
    max_side: int = 800,
    tie_epsilon: float = 1.0,
) -> Corner:
    """Return top-left of quietest logo box; BR on skip/tie/error."""
    width, height = canvas.size
    corners = _corner_positions(width, height, logo_w, logo_h, margin)
    br = corners["BR"]

    if logo_w <= 0 or logo_h <= 0:
        return br
    if width < logo_w + 2 * margin or height < logo_h + 2 * margin:
        return br
    if width * height < min_pixels:
        return br

    try:
        analysis = canvas.convert("RGB")
        scale = 1.0
        longest = max(width, height)
        if longest > max_side:
            scale = max_side / float(longest)
            new_w = max(1, int(round(width * scale)))
            new_h = max(1, int(round(height * scale)))
            analysis = analysis.resize((new_w, new_h), PILImage.Resampling.BILINEAR)

        gray = analysis.convert("L")
        s1, s2, aw, ah = _build_integrals(gray)
        stride = aw + 1

        scaled_lw = max(1, int(round(logo_w * scale)))
        scaled_lh = max(1, int(round(logo_h * scale)))
        scaled_margin = max(0, int(round(margin * scale)))

        scaled_corners = _corner_positions(aw, ah, scaled_lw, scaled_lh, scaled_margin)

        scores: dict[str, float] = {}
        for name, (sx, sy) in scaled_corners.items():
            if sx < 0 or sy < 0 or sx + scaled_lw > aw or sy + scaled_lh > ah:
                scores[name] = float("inf")
                continue
            scores[name] = _rect_variance(
                s1, s2, stride, sx, sy, scaled_lw, scaled_lh
            )

        best_name = min(scores, key=lambda n: (scores[n], 0 if n == "BR" else 1))
        best_var = scores[best_name]
        br_var = scores.get("BR", float("inf"))

        if abs(best_var - br_var) <= tie_epsilon:
            return br
        # Near-tie across all corners → prefer BR
        finite = [v for v in scores.values() if v != float("inf")]
        if finite and (max(finite) - min(finite)) <= tie_epsilon:
            return br

        return corners[best_name]
    except Exception:
        return br

"""Layer A: SAT O(1) lookup count + int64 overflow invariants."""
import pytest
from django.test import SimpleTestCase
from PIL import Image

from gallery.watermark_sat import _build_integrals, _rect_variance


@pytest.mark.dsa
class SatLookupInvariantTests(SimpleTestCase):
    def test_integrals_use_int64_and_white_sumsq_exact(self):
        img = Image.new("L", (800, 800), color=255)
        s1, s2, w, h = _build_integrals(img)
        self.assertEqual(s1.typecode, "q")
        self.assertEqual(s2.typecode, "q")
        # Full-image sumsq at 1-based (h, w)
        stride = w + 1
        total_sq = s2[h * stride + w]
        self.assertEqual(total_sq, 800 * 800 * (255 ** 2))
        var = _rect_variance(s1, s2, stride, 0, 0, 800, 800)
        self.assertGreaterEqual(var, 0.0)
        self.assertAlmostEqual(var, 0.0, places=6)

    def test_rect_variance_is_exactly_eight_lookups_any_box(self):
        img = Image.new("L", (200, 200), color=128)
        s1, s2, w, h = _build_integrals(img)
        stride = w + 1

        for box_w, box_h in ((50, 50), (100, 100), (180, 180)):
            counter = [0]
            _rect_variance(
                s1, s2, stride, 10, 10, box_w, box_h, lookup_counter=counter
            )
            self.assertEqual(
                counter[0],
                8,
                msg=f"Expected 8 SAT lookups for box {box_w}x{box_h}, got {counter[0]}",
            )

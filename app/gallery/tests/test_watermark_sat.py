"""Unit tests for SAT watermark corner selection."""
from django.test import SimpleTestCase, override_settings
from PIL import Image, ImageDraw

from gallery.tasks import _apply_workspace_watermark
from gallery.watermark_sat import (
    _build_integrals,
    _rect_variance,
    choose_quietest_corner,
)


class _FakeWorkspace:
    def __init__(self, logo: Image.Image, opacity: int = 100):
        self.watermark_opacity = opacity
        self._logo = logo

        class _Handle:
            def __init__(self, img):
                self._img = img

            def open(self, mode="rb"):
                import io

                buf = io.BytesIO()
                self._img.save(buf, format="PNG")
                buf.seek(0)
                return buf

        self.watermark_logo = _Handle(logo)


class WatermarkSatUnitTests(SimpleTestCase):
    def test_integral_buffers_use_int64(self):
        img = Image.new("L", (4, 4), color=255)
        s1, s2, w, h = _build_integrals(img)
        self.assertEqual(s1.typecode, "q")
        self.assertEqual(s2.typecode, "q")
        # All-white 4×4: sumsq = 16 * 255^2 — must stay positive (no 32-bit wrap).
        var = _rect_variance(s1, s2, w + 1, 0, 0, 4, 4)
        self.assertAlmostEqual(var, 0.0, places=6)
        self.assertGreater(s2[(h) * (w + 1) + w], 0)

    def test_integral_constant_image_zero_variance(self):
        img = Image.new("L", (4, 4), color=128)
        s1, s2, w, h = _build_integrals(img)
        var = _rect_variance(s1, s2, w + 1, 0, 0, 4, 4)
        self.assertAlmostEqual(var, 0.0, places=6)

    def test_quiet_br_selected(self):
        # Large enough to pass min_pixels when overridden
        img = Image.new("RGB", (400, 400), color=(128, 128, 128))
        draw = ImageDraw.Draw(img)
        # Noise in TL/TR/BL; leave BR flat
        for y in range(0, 200):
            for x in range(0, 200):
                v = 255 if (x + y) % 2 == 0 else 0
                draw.point((x, y), fill=(v, v, v))
        for y in range(0, 200):
            for x in range(200, 400):
                v = 255 if (x * 3 + y) % 2 == 0 else 0
                draw.point((x, y), fill=(v, v, v))
        for y in range(200, 400):
            for x in range(0, 200):
                v = 255 if (x + y * 5) % 2 == 0 else 0
                draw.point((x, y), fill=(v, v, v))

        pos = choose_quietest_corner(
            img, logo_w=80, logo_h=40, margin=10, min_pixels=1, max_side=800
        )
        self.assertEqual(pos, (400 - 80 - 10, 400 - 40 - 10))

    def test_quiet_tl_selected(self):
        img = Image.new("RGB", (400, 400), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Flat TL region; noise elsewhere
        draw.rectangle((0, 0, 199, 199), fill=(180, 180, 180))
        for y in range(0, 400):
            for x in range(200, 400):
                v = 255 if (x + y) % 2 == 0 else 0
                draw.point((x, y), fill=(v, v, v))
        for y in range(200, 400):
            for x in range(0, 200):
                v = 255 if (x * 7 + y) % 2 == 0 else 0
                draw.point((x, y), fill=(v, v, v))

        pos = choose_quietest_corner(
            img, logo_w=80, logo_h=40, margin=10, min_pixels=1, max_side=800
        )
        self.assertEqual(pos, (10, 10))

    def test_tie_prefers_br(self):
        img = Image.new("RGB", (400, 400), color=(100, 100, 100))
        pos = choose_quietest_corner(
            img, logo_w=80, logo_h=40, margin=10, min_pixels=1, tie_epsilon=1.0
        )
        self.assertEqual(pos, (400 - 80 - 10, 400 - 40 - 10))

    def test_tiny_image_skips_to_br(self):
        img = Image.new("RGB", (100, 100), color=(0, 0, 0))
        pos = choose_quietest_corner(
            img, logo_w=20, logo_h=10, margin=5, min_pixels=160_000
        )
        self.assertEqual(pos, (100 - 20 - 5, 100 - 10 - 5))

    @override_settings(PHOTO_WATERMARK_SAT_CORNER_SELECTION=False)
    def test_flag_off_keeps_bottom_right(self):
        canvas = Image.new("RGB", (400, 400), color=(0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 199, 199), fill=(200, 200, 200))
        for y in range(200, 400):
            for x in range(200, 400):
                v = 255 if (x + y) % 2 == 0 else 0
                draw.point((x, y), fill=(v, v, v))

        logo = Image.new("RGBA", (60, 30), color=(255, 0, 0, 255))
        workspace = _FakeWorkspace(logo)
        result = _apply_workspace_watermark(canvas, workspace)
        # Sample BR region — should have red influence
        br_x = 400 - 60 - max(24, 400 // 40)
        br_y = 400 - 30 - max(24, 400 // 40)
        pixel = result.getpixel((br_x + 5, br_y + 5))
        self.assertGreater(pixel[0], 100)

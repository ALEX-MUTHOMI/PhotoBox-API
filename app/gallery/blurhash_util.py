"""Blurhash LQIP encoder for masonry placeholders."""
from __future__ import annotations

from PIL import Image as PILImage


def encode_image_blurhash(image: PILImage.Image, x_components: int = 4, y_components: int = 3) -> str | None:
    """Encode a Pillow image to a blurhash string. Returns None on failure."""
    try:
        import blurhash
    except ImportError:
        return None

    try:
        rgb = image.convert("RGB")
        # Downscale for speed — blurhash only needs a tiny source.
        rgb.thumbnail((64, 64), PILImage.Resampling.LANCZOS)
        return blurhash.encode(rgb, x_components=x_components, y_components=y_components)
    except Exception:
        return None

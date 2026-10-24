from __future__ import annotations

import io
from PIL import Image

from app.services.images import (
    DEFAULT_IMAGE_PATH,
    get_default_announcement_image,
    optimize_image,
)


def test_optimize_image_empty():
    data, ext = optimize_image(b"")
    assert data == b""
    assert ext == "jpeg"


def test_optimize_image_resizes_large_dimensions():
    # Create a 2000x1500 RGB image
    img = Image.new("RGB", (2000, 1500), color=(200, 100, 50))
    raw = io.BytesIO()
    img.save(raw, format="JPEG", quality=100)
    raw_bytes = raw.getvalue()

    optimized, ext = optimize_image(raw_bytes, max_dimension=1200, quality=80)
    assert ext == "jpeg"
    with Image.open(io.BytesIO(optimized)) as opt_img:
        w, h = opt_img.size
        assert max(w, h) == 1200
        # 1200 / 2000 * 1500 = 900
        assert w == 1200
        assert h == 900


def test_optimize_image_handles_rgba():
    # Create an RGBA image
    img = Image.new("RGBA", (500, 500), color=(0, 0, 0, 0))
    raw = io.BytesIO()
    img.save(raw, format="PNG")
    raw_bytes = raw.getvalue()

    optimized, ext = optimize_image(raw_bytes, max_dimension=1200)
    assert ext == "png"
    with Image.open(io.BytesIO(optimized)) as opt_img:
        assert opt_img.mode in ("RGBA", "P")


def test_get_default_announcement_image():
    assert DEFAULT_IMAGE_PATH.exists()
    original_size = len(DEFAULT_IMAGE_PATH.read_bytes())
    optimized_bytes, ext = get_default_announcement_image()

    assert ext == "jpeg"
    assert len(optimized_bytes) > 0
    # Over 90% size reduction from original 1.35MB
    assert len(optimized_bytes) < 100_000
    assert len(optimized_bytes) < original_size * 0.1

    with Image.open(io.BytesIO(optimized_bytes)) as img:
        assert max(img.size) <= 1200

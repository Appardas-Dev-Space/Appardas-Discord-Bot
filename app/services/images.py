"""Image optimization and default assets for embeds and uploads."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from PIL import Image

log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "web" / "assets"
IMAGE_CANDIDATES = (
    "appardas-default.png",
    "appardas-default.jpeg",
    "appardas-default.jpg",
    "appardas-default.webp",
)


def get_default_image_path() -> Path:
    for name in IMAGE_CANDIDATES:
        candidate = ASSETS_DIR / name
        if candidate.exists():
            return candidate
    return ASSETS_DIR / "appardas-default.png"


DEFAULT_IMAGE_PATH = get_default_image_path()

# Max dimension (width or height) to resize down to (Discord embeds cap display at ~1200px)
MAX_DIMENSION = 1200
# Quality to avoid sending uncompressed 100% full quality detail
DEFAULT_QUALITY = 80


def optimize_image(
    data: bytes,
    max_dimension: int = MAX_DIMENSION,
    quality: int = DEFAULT_QUALITY,
) -> tuple[bytes, str]:
    """
    Optimize an image by downscaling dimensions if too large and compressing with
    sub-100% quality to dramatically reduce bandwidth and latency.
    Returns (optimized_bytes, extension).
    """
    if not data:
        return b"", "jpeg"
    try:
        with Image.open(io.BytesIO(data)) as img:
            has_alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            )

            # Resize if either dimension exceeds max_dimension
            w, h = img.size
            if max(w, h) > max_dimension:
                scale = max_dimension / max(w, h)
                new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            out = io.BytesIO()
            if has_alpha:
                img.save(out, format="PNG", optimize=True)
                ext = "png"
            else:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(out, format="JPEG", quality=quality, optimize=True)
                ext = "jpeg"
            return out.getvalue(), ext
    except Exception as exc:
        log.warning("Could not optimize image, using raw bytes: %s", exc)
        return data, "jpeg"


_CACHED_DEFAULT: tuple[float, str, tuple[bytes, str]] | None = None


def get_default_announcement_image() -> tuple[bytes, str]:
    """Get the pre-optimized default announcement image from assets."""
    global _CACHED_DEFAULT
    path = get_default_image_path()
    if not path.exists():
        return (b"", "jpeg")
    mtime = path.stat().st_mtime
    path_str = str(path)
    if (
        _CACHED_DEFAULT is not None
        and _CACHED_DEFAULT[0] == mtime
        and _CACHED_DEFAULT[1] == path_str
    ):
        return _CACHED_DEFAULT[2]
    raw_bytes = path.read_bytes()
    optimized = optimize_image(
        raw_bytes, max_dimension=MAX_DIMENSION, quality=DEFAULT_QUALITY
    )
    _CACHED_DEFAULT = (mtime, path_str, optimized)
    return optimized

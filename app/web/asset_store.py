"""Immutable, in-memory assets. Hash the bytes actually served, including fonts."""

from __future__ import annotations

import hashlib
from pathlib import Path

ASSET_DIR = Path(__file__).with_name("assets")


def _name(stem: str, extension: str, data: bytes) -> str:
    return f"{stem}.{hashlib.sha256(data).hexdigest()[:8]}.{extension}"


FONT_BYTES = (ASSET_DIR / "fonts/public-sans.woff2").read_bytes()
FONT_NAME = _name("public-sans", "woff2", FONT_BYTES)
TOKENS_BYTES = (ASSET_DIR / "tokens.css").read_bytes().replace(b"@FONT_URL@", f"/assets/{FONT_NAME}".encode())
TOKENS_NAME = _name("tokens", "css", TOKENS_BYTES)
CSS_BYTES = (ASSET_DIR / "app.css").read_bytes()
CSS_NAME = _name("app", "css", CSS_BYTES)
PUBLIC_CSS_BYTES = (ASSET_DIR / "public.css").read_bytes()
PUBLIC_CSS_NAME = _name("public", "css", PUBLIC_CSS_BYTES)

FAVICON_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="4" fill="#0c0a09"/><path d="M7 19h3v-6h3v11h3V8h3v11h6" fill="none" stroke="#d97706" stroke-width="2"/></svg>'
FAVICON_NAME = _name("favicon", "svg", FAVICON_BYTES)

# Loaded after the scripts exist; no bundler or filesystem access on a request.
JS_BYTES = (ASSET_DIR / "app.js").read_bytes()
JS_NAME = _name("app", "js", JS_BYTES)
PUBLIC_JS_BYTES = (ASSET_DIR / "public.js").read_bytes()
PUBLIC_JS_NAME = _name("public", "js", PUBLIC_JS_BYTES)

LOGO_BYTES = (ASSET_DIR / "appardas-logo.png").read_bytes()
LOGO_NAME = _name("appardas-logo", "png", LOGO_BYTES)

ASSETS = {
    FONT_NAME: (FONT_BYTES, "font/woff2"),
    TOKENS_NAME: (TOKENS_BYTES, "text/css"),
    CSS_NAME: (CSS_BYTES, "text/css"),
    PUBLIC_CSS_NAME: (PUBLIC_CSS_BYTES, "text/css"),
    JS_NAME: (JS_BYTES, "application/javascript"),
    PUBLIC_JS_NAME: (PUBLIC_JS_BYTES, "application/javascript"),
    FAVICON_NAME: (FAVICON_BYTES, "image/svg+xml"),
    LOGO_NAME: (LOGO_BYTES, "image/png"),
}

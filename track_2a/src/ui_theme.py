"""Colour palettes for the light and dark look, and the stylesheet built from them."""
from pathlib import Path
from typing import Dict

PALETTES: Dict[str, Dict[str, str]] = {
    "light": {
        "bg": "#F3F4F8", "surface": "#FFFFFF", "surface2": "#F7F8FB", "border": "#E3E6EE",
        "text": "#1A2233", "muted": "#5B6478",
        "blue": "#3B62C6", "green": "#06A88A", "yellow": "#E8A600",
        "red": "#E0434F", "grey": "#8A8F99", "orange": "#C25E00",
    },
    "dark": {
        "bg": "#121419", "surface": "#1C1F26", "surface2": "#232731", "border": "#2E333F",
        "text": "#E8EAF0", "muted": "#A3AABA",
        "blue": "#6C8EEB", "green": "#2FD1AE", "yellow": "#FBC53F",
        "red": "#FF6B76", "grey": "#9AA0AC", "orange": "#FF9440",
    },
}

_LINKS = (
    '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lato:wght@400;700;900&display=swap">'
)
# Streamlit may strip <link> from markdown, so the same sheets are also imported inside <style>.
_IMPORTS = (
    "@import url('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css');"
    "@import url('https://fonts.googleapis.com/css2?family=Lato:wght@400;700;900&display=swap');"
)
_CSS_FILE = Path(__file__).with_name("ui_theme.css")


def _luminance(color: str) -> float:
    channels = [int(color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two #RRGGBB colours."""
    lighter, darker = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def theme_css(mode: str) -> str:
    """Complete <link> and <style> block for 'light' or 'dark'."""
    palette = PALETTES["dark" if mode == "dark" else "light"]
    tokens = "".join(f"--{name}:{value};" for name, value in palette.items())
    scheme = "dark" if mode == "dark" else "light"
    return f"{_LINKS}<style>{_IMPORTS}:root{{{tokens}color-scheme:{scheme};}}\n{_CSS_FILE.read_text(encoding='utf-8')}</style>"

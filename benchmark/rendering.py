from __future__ import annotations

import base64
import hashlib
import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT, JPEG_QUALITY = 750, 1000, 75
RENDER_PROFILES = {
    "normal": {"font_size": 16, "lines_per_page": 45, "chars_per_line": 66, "line_height": 20},
    "dense": {"font_size": 8, "lines_per_page": 112, "chars_per_line": 128, "line_height": 8},
}
MARGIN_X = 36


def _render_profile(name: str) -> dict[str, int]:
    return RENDER_PROFILES.get(name, RENDER_PROFILES["normal"])


def paginate(text: str, profile: str = "normal") -> list[str]:
    settings = _render_profile(profile)
    lines: list[str] = []
    for raw in text.splitlines():
        lines.extend(textwrap.wrap(raw, width=settings["chars_per_line"], replace_whitespace=False, drop_whitespace=False) or [""])
    per_page = settings["lines_per_page"]
    return ["\n".join(lines[index:index + per_page]) for index in range(0, len(lines), per_page)] or [""]


def _font(size: int | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = size or RENDER_PROFILES["normal"]["font_size"]
    for candidate in ("/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_pages(page_texts: list[str], output_dir: Path, stem: str, profile: str = "normal") -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = _render_profile(profile)
    font = _font(settings["font_size"])
    paths: list[Path] = []
    for page_index, page_text in enumerate(page_texts):
        digest = hashlib.sha256(page_text.encode("utf-8")).hexdigest()[:12]
        path = output_dir / f"{stem}-p{page_index + 1:03d}-{profile}-{digest}.jpg"
        if not path.exists():
            image = Image.new("L", (WIDTH, HEIGHT), 252)
            draw = ImageDraw.Draw(image)
            draw.text((MARGIN_X, 24), f"CONTEXT PAGE {page_index + 1}/{len(page_texts)}", font=font, fill=35)
            y = 64
            for line in page_text.splitlines():
                draw.text((MARGIN_X, y), line, font=font, fill=25)
                y += settings["line_height"]
            image.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        paths.append(path)
    return paths


def data_url(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def verify_render_contract(text: str, profile: str = "normal") -> bool:
    return "\n".join(paginate(text, profile)).replace("\n", "") == text.replace("\n", "")

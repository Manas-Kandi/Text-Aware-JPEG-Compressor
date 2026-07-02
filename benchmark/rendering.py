from __future__ import annotations

import base64
import hashlib
import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT, FONT_SIZE, JPEG_QUALITY = 750, 1000, 16, 75
LINES_PER_PAGE = 45
CHARS_PER_LINE = 82


def paginate(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        lines.extend(textwrap.wrap(raw, width=CHARS_PER_LINE, replace_whitespace=False, drop_whitespace=False) or [""])
    return ["\n".join(lines[index:index + LINES_PER_PAGE]) for index in range(0, len(lines), LINES_PER_PAGE)] or [""]


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(candidate, FONT_SIZE)
        except OSError:
            pass
    return ImageFont.load_default()


def render_pages(page_texts: list[str], output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    font = _font()
    paths: list[Path] = []
    for page_index, page_text in enumerate(page_texts):
        digest = hashlib.sha256(page_text.encode("utf-8")).hexdigest()[:12]
        path = output_dir / f"{stem}-p{page_index + 1:03d}-{digest}.jpg"
        if not path.exists():
            image = Image.new("L", (WIDTH, HEIGHT), 252)
            draw = ImageDraw.Draw(image)
            draw.text((36, 24), f"CONTEXT PAGE {page_index + 1}/{len(page_texts)}", font=font, fill=35)
            y = 64
            for line in page_text.splitlines():
                draw.text((36, y), line, font=font, fill=25)
                y += 20
            image.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        paths.append(path)
    return paths


def data_url(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def verify_render_contract(text: str) -> bool:
    return "\n".join(paginate(text)).replace("\n", "") == text.replace("\n", "")

# Glyph

A dependency-free, text-aware JPEG compressor that runs entirely in the browser.

## Run

```sh
python3 -m http.server 4173
```

Open `http://localhost:4173`.

You can also open `index.html` directly. The app does not require a build step or web server.

## Compression approach

Glyph uses a small original algorithm designed for scans, screenshots, and photographed pages:

1. A Sobel-style edge scan estimates how much of the image contains probable text.
2. **Ink Guard** selectively sharpens high-frequency character strokes.
3. Flat, paper-like regions are gently color-quantized to remove JPEG-expensive scanner noise.
4. The compressor encodes several candidates and measures edge-contrast retention after decoding each one.
5. It chooses the smallest JPEG quality that passes the selected readability target.

### Model context mode

The aggressive **Model context** preset is designed for synthetic chat screenshots rather than photos. It caps output at 750×1000, removes chroma, strongly quantizes flat backgrounds, reinforces glyph edges, and searches down to JPEG quality 16. The result screen estimates the likely visual-token range from the resolutions reported in *Text or Pixels? It Takes Half* (Li, Lan, and Zhou, 2025).

JPEG byte size and LLM context cost are different objectives: the former affects storage and transport, while the latter is driven mainly by image resolution and the vision encoder's patch/token policy.

This is not OCR: no text content is extracted, and no file leaves the browser.

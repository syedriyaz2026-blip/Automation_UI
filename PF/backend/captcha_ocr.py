"""EPFO glyph classifier adapted from the supplied captchaautomation worker."""
from __future__ import annotations

import ctypes as C
from ctypes.util import find_library
from functools import lru_cache
from io import BytesIO
import json
import os
from pathlib import Path
import re

from PIL import Image

ROOT = Path(__file__).resolve().parent
CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def default_model_path() -> Path:
    python_model = ROOT / "models/ocr-glyphs-python.json"
    return python_model if python_model.exists() else ROOT / "models/ocr-glyphs.json"


def extract_glyphs(png: bytes) -> list[dict]:
    with Image.open(BytesIO(png)) as source:
        image = source.convert("L")
        width, height = image.size
        if (width, height) != (150, 50):
            raise ValueError(f"Expected original 150x50 CAPTCHA pixels; got {width}x{height}.")
        pixels = image.tobytes()
    mask = bytearray(width * height)
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        background = sorted(row)[width // 2]
        for x, value in enumerate(row):
            mask[y * width + x] = int(value < background * 0.625)
    groups = []
    start = None
    for x in range(width + 1):
        count = sum(mask[y * width + x] for y in range(height)) if x < width else 0
        if count and start is None:
            start = x
        if not count and start is not None:
            groups.append([start, x - 1])
            start = None
    index = 1
    while index < len(groups):
        previous, current = groups[index - 1], groups[index]
        gap = current[0] - previous[1] - 1
        if gap <= 2 and (previous[1] - previous[0] < 2 or current[1] - current[0] < 2):
            previous[1] = current[1]
            groups.pop(index)
        else:
            index += 1
    if len(groups) != 5:
        raise ValueError(f"Detected {len(groups)} glyphs instead of 5; review the CAPTCHA.")
    glyphs = []
    for left, right in groups:
        rows = [y for y in range(height) if any(mask[y * width + x] for x in range(left, right + 1))]
        top, bottom = min(rows), max(rows)
        bits = "".join(str(mask[y * width + x]) for y in range(top, bottom + 1) for x in range(left, right + 1))
        glyphs.append({"width": right - left + 1, "height": bottom - top + 1, "bits": bits})
    return glyphs


@lru_cache(maxsize=2048)
def _points(width: int, height: int, bits: str) -> frozenset:
    ox, oy = (24 - width) // 2, (24 - height) // 2
    return frozenset((x + ox, y + oy) for y in range(height) for x in range(width) if bits[y * width + x] == "1")


def glyph_distance(a: dict, b: dict) -> float:
    pa = _points(a["width"], a["height"], a["bits"])
    pb = _points(b["width"], b["height"], b["bits"])
    best = 1.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            overlap = sum((x - dx, y - dy) in pb for x, y in pa)
            best = min(best, 1 - overlap / (len(pa) + len(pb) - overlap))
    return best


def rank_templates(glyph: dict, templates: list[dict], exclude_source: str | None = None) -> list[dict]:
    scores = {}
    for template in templates:
        if exclude_source and template["source"] == exclude_source:
            continue
        distance = glyph_distance(glyph, template)
        label = template["label"]
        scores[label] = min(scores.get(label, 1), distance)
    return sorted(({"value": value, "distance": distance} for value, distance in scores.items()), key=lambda item: item["distance"])


def glyph_image(glyph: dict, padding: int = 4, scale: int = 6, line_height: int | None = None) -> Image.Image:
    width = glyph["width"] + padding * 2
    height = line_height or glyph["height"] + padding * 2
    pixels = bytearray([255] * (width * height))
    top = (height - glyph["height"]) // 2
    for y in range(glyph["height"]):
        for x in range(glyph["width"]):
            if glyph["bits"][y * glyph["width"] + x] == "1":
                pixels[(y + top) * width + x + padding] = 0
    return Image.frombytes("L", (width, height), bytes(pixels)).resize((width * scale, height * scale), Image.Resampling.LANCZOS)


class TesseractEngine:
    """Own one native C API instance and free every returned text allocation."""

    def __init__(self):
        configured = os.environ.get("TESSERACT_LIBRARY")
        candidates = [configured] if configured else []
        candidates.extend(str(p) for p in [
            ROOT / ".tools/tesseract/libtesseract-5.dll",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/libtesseract-5.dll",
            ROOT.parents[2] / "PF_auto_f/PF_auto_f/backend/pf_automation/captchaautomation/.tools/tesseract/libtesseract-5.dll",
        ] if p.exists())
        found = find_library("tesseract")
        if found:
            candidates.append(found)
        if not candidates:
            raise RuntimeError("Tesseract runtime not found. Install Tesseract or set TESSERACT_LIBRARY to its shared library.")
        self._directory = None
        if os.name == "nt":
            self._directory = os.add_dll_directory(str(Path(candidates[0]).resolve().parent))
        self.lib = C.CDLL(candidates[0])
        self.api = None
        definitions = {
            "TessVersion": ([], C.c_char_p),
            "TessBaseAPICreate": ([], C.c_void_p),
            "TessBaseAPIInit2": ([C.c_void_p, C.c_char_p, C.c_char_p, C.c_int], C.c_int),
            "TessBaseAPISetVariable": ([C.c_void_p, C.c_char_p, C.c_char_p], C.c_int),
            "TessBaseAPISetPageSegMode": ([C.c_void_p, C.c_int], None),
            "TessBaseAPISetImage": ([C.c_void_p, C.POINTER(C.c_ubyte), C.c_int, C.c_int, C.c_int, C.c_int], None),
            "TessBaseAPISetSourceResolution": ([C.c_void_p, C.c_int], None),
            "TessBaseAPIRecognize": ([C.c_void_p, C.c_void_p], C.c_int),
            "TessBaseAPIGetUTF8Text": ([C.c_void_p], C.c_void_p),
            "TessBaseAPIMeanTextConf": ([C.c_void_p], C.c_int),
            "TessDeleteText": ([C.c_void_p], None),
            "TessBaseAPIClear": ([C.c_void_p], None),
            "TessBaseAPIEnd": ([C.c_void_p], None),
            "TessBaseAPIDelete": ([C.c_void_p], None),
        }
        for name, (arguments, result) in definitions.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = arguments, result
        data_dir = Path(os.environ.get("TESSDATA_DIR", str(ROOT.parents[1] / "Platform/backend")))
        if not (data_dir / "eng.traineddata").exists():
            raise RuntimeError("eng.traineddata is missing. Run python/setup_data.py first or set TESSDATA_DIR.")
        self.api = self.lib.TessBaseAPICreate()
        if self.lib.TessBaseAPIInit2(self.api, str(data_dir).encode("utf-8"), b"eng", 1):
            self.close()
            raise RuntimeError("Tesseract could not load eng.traineddata.")
        self.lib.TessBaseAPISetVariable(self.api, b"tessedit_char_whitelist", CHARS.encode())
        self.lib.TessBaseAPISetVariable(self.api, b"user_defined_dpi", b"300")
        self.version = self.lib.TessVersion().decode()

    def recognize(self, image: Image.Image, mode: int) -> tuple[str, int]:
        data = image.convert("L").tobytes()
        buffer = (C.c_ubyte * len(data)).from_buffer_copy(data)
        self.lib.TessBaseAPISetPageSegMode(self.api, mode)
        self.lib.TessBaseAPISetImage(self.api, buffer, image.width, image.height, 1, image.width)
        self.lib.TessBaseAPISetSourceResolution(self.api, 300)
        if self.lib.TessBaseAPIRecognize(self.api, None):
            raise RuntimeError("Tesseract recognition failed.")
        pointer = self.lib.TessBaseAPIGetUTF8Text(self.api)
        try:
            text = C.string_at(pointer).decode("utf-8") if pointer else ""
            confidence = self.lib.TessBaseAPIMeanTextConf(self.api)
        finally:
            if pointer:
                self.lib.TessDeleteText(pointer)
            self.lib.TessBaseAPIClear(self.api)
        return text, confidence

    def close(self):
        if self.api:
            self.lib.TessBaseAPIEnd(self.api)
            self.lib.TessBaseAPIDelete(self.api)
            self.api = None
        if self._directory:
            self._directory.close()
            self._directory = None


class Recognizer:
    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or default_model_path()
        self.model = json.loads(self.model_path.read_text(encoding="utf-8"))
        self.engine = None

    def recognize(self, png: bytes, exclude_source: str | None = None) -> dict:
        characters = []
        for glyph in extract_glyphs(png):
            matches = rank_templates(glyph, self.model["templates"], exclude_source)
            best = matches[0] if matches else None
            second_distance = matches[1]["distance"] if len(matches) > 1 else 1
            if best and best["distance"] <= self.model["maxDistance"] and second_distance - best["distance"] >= self.model["minMargin"]:
                characters.append({"value": best["value"], "source": "template", "similarity": round(1 - best["distance"], 4), "needsReview": False})
                continue
            if self.engine is None:
                self.engine = TesseractEngine()
            variants = [glyph_image(glyph, 3, 6), glyph_image(glyph, 4, 6, 24), glyph_image(glyph, 2, 8)]
            votes = {}
            for image in variants:
                for mode in (10, 8, 13):
                    text, confidence = self.engine.recognize(image, mode)
                    value = "".join(text.split())
                    if re.fullmatch(r"[A-Z][a-z]", value) and value[0].lower() == value[1]:
                        value = value[1] if glyph["height"] <= 10 else value[0]
                    if not re.fullmatch(r"[A-Za-z0-9]", value) or confidence < 25:
                        continue
                    previous = votes.setdefault(value, {"score": 0, "confidence": 0, "count": 0})
                    previous["score"] += confidence - 20
                    previous["confidence"] = max(previous["confidence"], confidence)
                    previous["count"] += 1
            if not votes:
                raise ValueError("OCR could not read every character. Review the CAPTCHA in the browser.")
            value, vote = max(votes.items(), key=lambda item: item[1]["score"])
            characters.append({"value": value, "source": "tesseract", "confidence": vote["confidence"], "votes": vote["count"], "needsReview": True})
        return {"text": "".join(c["value"] for c in characters), "characters": characters, "needsReview": any(c["needsReview"] for c in characters)}

    def close(self):
        if self.engine:
            self.engine.close()
            self.engine = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

"""Read the five-character EPFO image challenge from browser pixels in memory."""

from __future__ import annotations

from functools import lru_cache

from PF.backend.captcha_ocr import Recognizer


class CaptchaReadError(RuntimeError):
    """The current image could not be read with enough confidence to submit."""


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr import RapidOCR

    return RapidOCR()


def read_captcha(image_bytes: bytes, expected_length: int = 5) -> str:
    """Use the supplied trained glyph model; fall back to RapidOCR if needed."""
    try:
        with Recognizer() as recognizer:
            value = recognizer.recognize(image_bytes)["text"]
        if len(value) == expected_length:
            return value
    except Exception:
        pass
    return _read_captcha_rapidocr(image_bytes, expected_length)


def _read_captcha_rapidocr(image_bytes: bytes, expected_length: int = 5) -> str:
    """Recognize each glyph from left to right, including vertically offset glyphs.

    The live EPFO image separates its characters horizontally but offsets them
    vertically. Whole-line OCR can return the characters in reading-row order.
    """
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            raise CaptchaReadError("CAPTCHA image is empty or invalid.")

        # The challenge has a gradient that sometimes darkens below a fixed
        # threshold. Try progressively darker masks until exactly five glyphs
        # remain, leaving the background out of the connected components.
        engine = _ocr_engine()
        best_answer = ""
        best_quality = (0.0, 0.0)
        found_expected_count = False
        for threshold in range(100, 19, -10):
            candidate_mask = (image < threshold).astype("uint8")
            _, _, stats, _ = cv2.connectedComponentsWithStats(candidate_mask, 8)
            glyphs = sorted(
                (tuple(map(int, item)) for item in stats[1:] if item[4] >= 18 and item[2] >= 3 and item[3] >= 6 and item[2] < image.shape[1] // 3),
                key=lambda item: item[0],
            )
            if len(glyphs) != expected_length:
                continue
            found_expected_count = True
            answer = []
            scores = []
            for x, y, width, height, _ in glyphs:
                ink = (candidate_mask[y:y + height, x:x + width] * 255).astype("uint8")
                ink = cv2.resize(ink, (width * 3, height * 3), interpolation=cv2.INTER_NEAREST)
                glyph_height, glyph_width = ink.shape
                crop = np.full((glyph_height + 20, glyph_width + 20), 255, dtype="uint8")
                crop[10:10 + glyph_height, 10:10 + glyph_width] = 255 - ink
                prediction = engine(crop, use_det=False, use_cls=False)
                text = (prediction.txts or ("",))[0]
                score = float((prediction.scores or (0.0,))[0])
                if len(text) != 1 or not text.isascii() or not text.isalnum() or score < 0.60:
                    break
                answer.append(text)
                scores.append(score)
            if len(answer) == expected_length:
                quality = (min(scores), sum(scores) / len(scores))
                if quality > best_quality:
                    best_answer, best_quality = "".join(answer), quality
        if best_answer:
            return best_answer
        if not found_expected_count:
            raise CaptchaReadError("CAPTCHA character count could not be verified.")
        raise CaptchaReadError("CAPTCHA character could not be read confidently.")
    except CaptchaReadError:
        raise
    except Exception as exc:
        raise CaptchaReadError(f"CAPTCHA OCR is unavailable ({type(exc).__name__}).") from exc

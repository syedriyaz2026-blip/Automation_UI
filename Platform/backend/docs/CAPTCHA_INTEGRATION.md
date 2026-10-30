# EPFO CAPTCHA integration

The REAL PF worker reads the five-character CAPTCHA image displayed in its
current browser session. `captcha_service.py` locates the image, captures its
original pixels through a canvas, and tracks its fingerprint. The supplied
`captcha_ocr.py` glyph model recognizes each character; `captcha_solver.py`
uses RapidOCR if that recognizer cannot read the image.
The answer stays in memory while `EpfoBot` fills the configured input and
clicks **Sign In**. The worker continues only after it sees the expected
authenticated dashboard.

The image and input have separate selector keys in `config/selectors.json`:
`captcha` for the image, `captcha_input` for the field. Keep them in sync with
portal changes. The current solver expects five characters; an unreadable or
changed format fails explicitly. A rejected answer is retried only when EPFO
provides a changed image, up to `max_captcha_attempts` in `config/settings.json`.

Install `backend/requirements.txt` before REAL runs. The trained model is
bundled in `PF/backend/models/`. The supplied native Tesseract runtime is
detected in the sibling `PF_auto_f` project; a standard Tesseract installation
or `TESSERACT_LIBRARY` also works. Both API and desktop preflight check for
RapidOCR as a fallback. The recognized text is not logged or persisted.

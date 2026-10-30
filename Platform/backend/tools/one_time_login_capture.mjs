// Intentionally disabled.
//
// Real EPFO CAPTCHA solving or automatic submission is not part of this
// application. The supported workflow is implemented by epfo_bot.py and
// captcha_service.py: detect the CAPTCHA, capture evidence, wait for the
// operator to complete the portal checkpoint, and continue only after the
// authenticated dashboard is detected.
//
// Local end-to-end automation is available through EPFO_AUTH_MODE=MOCK.
throw new Error(
  "This standalone real-portal helper is disabled. Use the application backend; " +
  "REAL mode requires a manual CAPTCHA checkpoint, while MOCK mode is fully local."
);

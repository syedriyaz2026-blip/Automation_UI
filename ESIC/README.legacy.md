# ESIC / ESI module

The product-facing module key is `ESI`; the repository folder is `ESIC` to
match the statutory system name. ESI supports scoped API intake only; Excel
upload is disabled,
date and field normalization, mandatory-field validation, duplicate detection,
audit history, and pending automation handoff. It is intentionally independent
from PF storage and module permissions.

The current service does not automatically solve or bypass a real portal
CAPTCHA. Any live portal checkpoint remains supervised and the automation
handoff records a pending state.

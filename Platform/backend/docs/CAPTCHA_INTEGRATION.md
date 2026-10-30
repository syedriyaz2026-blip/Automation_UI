# CAPTCHA integration boundary

The backend now treats CAPTCHA as an operator checkpoint, not as a value to be
automated. `captcha_service.py` is responsible for four safe operations:

- detect a configured visible CAPTCHA control;
- classify the configured image challenge;
- fingerprint the rendered image to detect a refresh/change;
- save redacted diagnostic evidence and expose an explicit expiry state.

`EpfoBot` uses the service during authentication. It pauses in
`WAITING_FOR_OPERATOR`, and the operator completes the CAPTCHA and submits the
portal form in the visible browser. No OCR, CAPTCHA answer extraction, token
injection, external solver, audio challenge, cookie reuse, or credential logging
is implemented.

## Why the component is intentionally limited

The existing EPFO employer login is protected by a portal CAPTCHA. Authorized
credentials do not make bypassing that control safe or supported. The supported
automation boundary is therefore: prepare the browser, detect the checkpoint,
capture evidence, wait for a human, and verify the authenticated dashboard.

For production, an organization should request an officially supported
server-to-server integration or approved filing channel from EPFO. Until that
contract is provided, the browser worker must retain this manual checkpoint.

## Extension points

Portal changes should normally require updates only to `config/selectors.json`
or a selector profile. A future officially approved integration should be added
as a separate authenticated provider, with the same `AuthState` contract, and
must not be implemented by bypassing the browser CAPTCHA.

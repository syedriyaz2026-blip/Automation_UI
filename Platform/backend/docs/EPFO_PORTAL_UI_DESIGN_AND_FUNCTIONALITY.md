# EPFO Portal Reference: UI, Design, and Functional Contract

This document records the visual and functional reference used for the local operations product. The live reference is the EPFO Employer portal: `https://unifiedportal-emp.epfindia.gov.in/epfo/`.

The local application should be clearly labelled as an internal operations tool. It may use the portal’s information hierarchy and visual language, but it must not misrepresent itself as the official EPFO website.

## Reference design language

The portal uses a government-service visual style rather than a modern glass-dashboard style:

- restrained blue/navy branding;
- white content surfaces with light grey borders and shadows;
- Bootstrap-style rows, columns, compact form controls, alerts, tables, and buttons;
- small utility text for technical help and accessibility controls;
- orange/yellow highlights for contact, warnings, and attention states;
- blue, green, and red status treatments;
- dense information layout with a prominent sign-in panel and supporting notices.

## Typography

The linked portal styles declare these font families:

| Use | Font family | Fallback |
|---|---|---|
| General UI | `Open Sans` | `sans-serif` |
| Primary application text | `Proxima Nova Regular` | `Segoe UI`, `Roboto`, `Arial` |
| Headings/feature text | `Raleway` | `sans-serif` |
| Effects/legacy utility text | `Tahoma` or `Roboto` | `Verdana`, `sans-serif` |
| Icons | Font Awesome | icon fallback |

Recommended local implementation tokens:

```css
--font-body: "Open Sans", "Segoe UI", Roboto, Arial, sans-serif;
--font-heading: Raleway, "Segoe UI", Arial, sans-serif;
--font-utility: Tahoma, Verdana, sans-serif;
```

Use 12–14px for dense form and utility text, 15–18px for section titles, and 22–28px for page-level headings. Keep line height between 1.35 and 1.6 for readable validation/error messages.

## Reference colour tokens

The portal’s linked styles use a broad palette. These are the primary semantic tokens to retain in the local implementation:

| Semantic role | Token | Reference values |
|---|---|---|
| Primary government blue | `--epfo-blue` | `#0A54AE`, `#09589B`, `#112755` |
| Deep header navy | `--epfo-navy` | `#112755`, `#293E6A` |
| Link blue | `--epfo-link` | `#155396`, `#4F82E9` |
| Header/neutral grey | `--epfo-grey` | `#F2F2F2`, `#707374`, `#E1E1E1` |
| Surface white | `--epfo-surface` | `#FFFFFF`, `#FFF` |
| Contact/orange | `--epfo-orange` | `#EF880F`, `#FF6A00`, `#F6B247` |
| Warning yellow | `--epfo-warning` | `#FFE674`, `#F8B500`, `#FCEABB` |
| Success green | `--epfo-success` | `#6CC829`, `#008D4D`, `#5F9358` |
| Error red | `--epfo-danger` | `#FF6464`, `#DD454C`, `#B42318` |
| Information blue | `--epfo-info` | `#F0F9FF`, `#A1DBFF`, `#66B6FC` |
| Body text | `--epfo-text` | `#424242`, `#333333`, `#20354D` |
| Muted text | `--epfo-muted` | `#777777`, `#808080`, `#97A0B3` |
| Border | `--epfo-border` | `#CCCCCC`, `#D2D6DE`, `#EAEAEA` |
| Shadow | `--epfo-shadow` | `rgba(0,0,0,.10)` to `rgba(0,0,0,.30)` |

The local operations dashboard currently keeps its separate purple brand system. If visual parity with the reference portal is requested, switch the theme tokens rather than scattering colour changes through component CSS.

## Reference layout and visible functionality

### 1. Utility header

- Government of India link.
- Screen Reader Access.
- Technical help phone number and working hours.
- Contact Us link.
- Font increase, normal, and decrease controls.
- Social links for Facebook and X/Twitter.

### 2. Brand header

- EPFO official logo.
- EPFO title lockup.
- G20 mark.
- Sticky, full-width header treatment.
- Thin border separating utility and content areas.

### 3. Employer sign-in

- Employer Login heading.
- Username field with user icon.
- Password field with lock icon and show/hide control.
- Live CAPTCHA image.
- CAPTCHA input field.
- Sign In action.
- Reset action.
- Forgot Password link.
- Unlock Account link.
- Employer Sign In and Uncovered Principal Employer Sign In links.

The automation product must preserve the live CAPTCHA as a manual security checkpoint. It must capture the checkpoint for evidence, pause, and continue only after the operator completes it. It must never OCR, crack, bypass, or submit a real CAPTCHA automatically.

### 4. Information content

- Welcome Employers area.
- Scrolling or highlighted notice/news content.
- What's New panel.
- Important Links panel.
- UMANG, ECR, multi-banking, and UAN-related notices.
- Pre-OLRE modal with an eligibility note and Proceed/Close actions.
- Security alert modal warning users not to share credentials.

### 5. Footer

- EPFO ownership/development statement.
- Last Updated information.

## Local automation product screens

The local product adds operational screens around the portal workflow:

1. **Application login** - local JWT-protected operator login; never the PF employer credential form.
2. **Company profile** - select a company and load its field mapping, portal URL, selector profile, and credential status.
3. **API inbox** - view new records, source row, employee code, company, DOJ, UAN, and current processing status.
4. **Pre-insertion validation** - validate mandatory fields, formats, dates, checksums, duplicates, company mapping, and conditional spouse rules before browser startup.
5. **Validation results** - show all errors for each row; no silent trimming or correction.
6. **Start insertion** - enabled only when all selected rows are valid and the operator explicitly starts the run.
7. **Live process** - show authentication state, current employee, row progress, events, pause/stop state, and safe row boundaries.
8. **CAPTCHA checkpoint** - show the captured live checkpoint and clear manual-action instruction.
9. **Registration result** - show `Registered - Pending Approval`; the automation stops before Approve or Reject.
10. **Error handover** - show row, employee code, category, workflow step, message, and proof links.
11. **Email notification** - show whether SMTP accepted the consolidated failure email and how many evidence files were attached.
12. **Retry email only** - retry notification delivery without rerunning browser automation.
13. **Audit/evidence** - expose run JSON, audit JSONL, error JSON, screenshots, and handover JSON through approved local endpoints.
14. **Recovery state** - after a service restart, unfinished runs become `RECOVERY_REQUIRED`; the system never resumes blindly.

## State and component rules

- **Primary action:** blue/indigo, explicit label, disabled until prerequisites pass.
- **Success:** green status and completion message.
- **Warning:** yellow/orange status with the next operator action.
- **Error:** red status, exact row and step, evidence link, and handover category.
- **Disabled:** reduced contrast but still readable; include the reason in helper text or tooltip.
- **Loading:** retain the current context and show the active operation; do not replace the whole page with a blank spinner.
- **Empty state:** explain why there are no records and provide the next safe action.
- **Responsive behavior:** collapse the sidebar and stack panels below 1100px; keep tables horizontally scrollable; preserve keyboard access.

## Accessibility and operational requirements

- Every input has a visible label and an accessible name.
- Focus state must be visible for keyboard users.
- Error messages must be associated with the affected row/field.
- Do not rely on colour alone for status; include text labels.
- Preserve font scaling controls or browser zoom compatibility.
- Do not expose passwords, CAPTCHA values, OTPs, or credentials in logs, screenshots, email bodies, or persisted JSON.
- Keep the approval boundary visible on every run screen.

## Evidence limits

The live page source and linked CSS were inspected for this inventory. A browser screenshot audit was not completed in this session because the available browser surface was unavailable. Final pixel-level parity should be verified with captured screenshots once a browser surface is available.

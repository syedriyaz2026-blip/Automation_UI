# ESI backend

The ESI backend owns the employee contract, source adapter, scoped inbox,
duplicate/update lifecycle, reports, and the configurable portal workflow.

`esi_portal_workflow.py` follows the stages recorded in the supplied `ESIC 1.zip`:

1. Before Login
2. After Login
3. Duplicate Check
4. Identity Details
5. Address Details
6. Nominee Details
7. Bank Details
8. Consent and Submission
9. ESIC Number Retrieval
10. Report and e-PehChan

Portal selectors are supplied explicitly by an ESI selector profile. The
workflow fails closed when a selector is missing; it does not reuse PF
selectors or guess controls. Username and password entry can be automated,
but CAPTCHA remains a human checkpoint.

The archive provides `empData.json`, reports, assets, and an execution log,
but no recoverable Java source or compiled worker classes. Consequently, the
exact original selectors and portal-specific result controls must be supplied
and reviewed before enabling a live ESI portal run.

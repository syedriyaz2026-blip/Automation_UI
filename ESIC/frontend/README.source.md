# ESIC frontend boundary

The shared React application renders ESI from the `ESI` module key and checks
the backend module grant before enabling any ESI operation. ESI has its own
intake controls, validation status, source-sync counts/timestamp, account
configuration surface, queue status, evidence/report links, and field map;
these are not mixed with PF state.

The ESI queue displays the converted mandatory fields and provides an
expandable “View all ESI fields” section for the full employee, address,
nominee, dispensary, bank, ESIC, and source-contract payload. Workbook upload
is locked until mandatory-column and row validation succeeds. Source sync
shows the latest-48-hour result and validation counts before records enter the
scoped queue.

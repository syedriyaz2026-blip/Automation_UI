# Continuous ESI monitoring

The platform UI is presentation-only. No operator start/stop action is
required. A background machine process must run continuously and expose the
ESI API routes configured in `frontend/api.js`.

The UI automatically:

- checks API health on load;
- fetches the current scoped queue;
- refreshes every 30 seconds;
- displays the current process stage and record status;
- shows `API not connected` when the monitor/API is unavailable;
- never starts or stops the worker from the platform screen.

The worker is responsible for fetching new onboarded records, validating,
deduplicating, processing eligible records, updating audit/evidence, and
publishing status to the platform API. The platform must not use sample data as
live data; sample records are only an offline fallback when the API is down.

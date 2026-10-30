# Automation_UI platform

The application is organized as five direct module folders: Platform, PF,
ESIC, LWF, and PT. Start the shared API from the repository root with:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m Platform.backend.api_service
```

Start the shared frontend from `Platform/frontend` with `npm run dev`.
The Platform shell composes the enabled statutory module surfaces while each
module owns its backend boundary and module manifest.

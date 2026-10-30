# Platform frontend

This is the runnable React/Vite shell. It owns authentication, navigation,
RBAC-aware module selection, company scope, intake, queue, evidence, reports,
and the shared design system. PF and ESIC frontend boundaries are represented
by their module manifests and are composed by this host to avoid duplicating
the working application shell.

Run from this directory with `npm run dev` or `npm run build`.

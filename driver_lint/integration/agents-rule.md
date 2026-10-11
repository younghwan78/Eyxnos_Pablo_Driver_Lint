<!-- driver-lint:start -->
## Independent Pablo IS / GDC linters

Select the driver being edited. Read that package's CLAUDE.md and active profile:
- Pablo IS: driver_lint/pablo_is/config/policy.json;
  run python3 driver_lint/pablo_is/lint.py scan.
- GDC: driver_lint/gdc/config/policy.json;
  run python3 driver_lint/gdc/lint.py scan.

Pablo IS excludes post/gdc and GDC KUnit sources. GDC selects its own IP version
and does not inherit a Pablo family selection. Shared build/board files have
separate registered contracts. Preserve common API/lifecycle/PM responsibilities;
implement HW differences and their build/profile mappings with evidence.

Hooks/plugins, Git gates and CI are selectable with --component pablo-is|gdc|all.
For changes spanning both drivers, python3 driver_lint/lint.py scan coordinates
both independent checks and reports each outcome. A single-driver task runs that
owner's linter. Let installed commit gates run; do not disable hooks, bypass CI,
weaken policy or reset references to hide failures. Errors and inability to verify
reject commits. Required server statuses are driver-lint-pablo-is / driver-lint-gdc.
Include warnings, profile, build/KUnit/HW results and pending validation in review.
This source release has no root Git repository and uses derived owner references.
<!-- driver-lint:end -->

---
paths:
  - "exynos/external-modules/camera/**"
  - "exynos/device/**/configs/*.fragment"
---

# pablo-is engineering contract

Read `driver_lint/pablo_is/CLAUDE.md` and its own active profile.
This linter owns Pablo IS and its IPs; GDC production sources and GDC KUnit files are excluded.
Shared Kconfig/Bazel/board files are checked only for this driver's registered contracts.
Preserve common API/lifecycle/PM boundaries and Linux style. HW versions are project data.
Run `python3 driver_lint/pablo_is/lint.py scan` before completion, and
`scan --profile-only --checkpatch off` after HW/build profile changes.
Report lint warnings, build/KUnit and pending HW validation. Do not disable gates
or reset an original source reference to hide a change.

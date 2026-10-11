---
paths:
  - "exynos/external-modules/camera/camera/post/gdc/**"
  - "exynos/external-modules/camera/camera/testing/kunit/post/*gdc*"
  - "exynos/external-modules/camera/BUILD.bazel"
  - "exynos/external-modules/camera/camera/Kconfig"
  - "exynos/device/**/configs/*.fragment"
---

# gdc engineering contract

Read `driver_lint/gdc/CLAUDE.md` and its own active profile.
This linter owns GDC production sources and GDC KUnit files; Pablo IS sources and family selection are outside its ownership.
Shared Kconfig/Bazel/board files are checked only for this driver's registered contracts.
Preserve common API/lifecycle/PM boundaries and Linux style. HW versions are project data.
Run `python3 driver_lint/gdc/lint.py scan` before completion, and
`scan --profile-only --checkpatch off` after HW/build profile changes.
Report lint warnings, build/KUnit and pending HW validation. Do not disable gates
or reset an original source reference to hide a change.

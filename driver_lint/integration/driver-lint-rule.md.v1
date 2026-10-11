---
paths:
  - "exynos/external-modules/camera/**"
  - "exynos/device/**/configs/*.fragment"
---

# Camera/GDC common and HW-specific change rules

Read `driver_lint/CLAUDE.md` and the project profile selected by
`driver_lint/config/policy.json`. Use `doctor` to see the resolved versions.
Keep common lifecycle/buffer/PM policy and API contracts separate from
HW-specific parameter/register implementations. Pablo and individual IP
versions are independent; the S947B profile is one worked example.
For an intended new SoC/HW migration, update the profile and relevant HW/build
code from source/spec evidence and run `scan --profile-only` plus normal `scan`.
Set `policy.profile` to the intended project so hooks and CLI agree.

Preserve core → HW API → register boundaries and the existing tab-based Linux
style. Give the smallest reviewable change and keep unrelated formatting out.
Run `python3 driver_lint/lint.py scan` and report errors, warnings, build/KUnit
status, and any hardware verification that remains. Profile updates for an
intended HW change are expected. Document the reason and evidence; do not weaken
common rules, invent compatibility, or reset a reference to hide a violation.

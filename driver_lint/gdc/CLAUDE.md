# GDC developer contract

Ownership: GDC core, selected HW API/registers, HDR10+/VOTF adapters and GDC KUnit sources. Pablo IS source and its family selection are outside this scope.
Read `driver_lint/CLAUDE.md` for shared workflow, then this package's
`config/policy.json` and selected `config/profiles/` HW data.

Common architecture/style/severity/exception policy belongs to this package.
Modify the selected HW implementation and its profile/build contracts when HW
changes. Versions are project data; the S947B profile is an example. Select GDC IP/register compatibility independently of Pablo family; do not add a family selection to this profile.

Preserve common API/buffer/lifecycle/PM responsibilities, Linux tabs, helpers,
naming and error unwinds. Program registers in the HW API. Shared build/board
files require this owner's contract review; changes affecting both owners need
both checks and reviewers. Run:

```bash
python3 driver_lint/gdc/lint.py scan
python3 driver_lint/gdc/lint.py scan --profile-only --checkpatch off
```

The profile-only command is a HW/build mapping check; normal scan checks source.
In Git, `python3 driver_lint/gdc/commit_gate.py` reads actual index bytes
and settings. Install pre-commit with `scripts/install_git.py --component gdc`.
Required CI status: `driver-lint-gdc`. Do not bypass gates, weaken policy or
reset an approved baseline to hide violations. Error/verification failures block;
review warnings, ABI impact, build/KUnit and actual HW validation separately.

Report this owner's lint result and any cross-driver changes/pending tests.

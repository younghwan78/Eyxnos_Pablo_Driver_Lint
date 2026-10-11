# Pablo IS developer contract

Ownership: Pablo IS core, interface, ischain, hardware APIs/registers, sensor/CSI, libraries and non-GDC tests. GDC source/test paths are excluded.
Read `driver_lint/CLAUDE.md` for shared workflow, then this package's
`config/policy.json` and selected `config/profiles/` HW data.

Common architecture/style/severity/exception policy belongs to this package.
Modify the selected HW implementation and its profile/build contracts when HW
changes. Versions are project data; the S947B profile is an example. Pablo family and individual IS IP versions are independent.

Preserve common API/buffer/lifecycle/PM responsibilities, Linux tabs, helpers,
naming and error unwinds. Program registers in the HW API. Shared build/board
files require this owner's contract review; changes affecting both owners need
both checks and reviewers. Run:

```bash
python3 driver_lint/pablo_is/lint.py scan
python3 driver_lint/pablo_is/lint.py scan --profile-only --checkpatch off
```

The profile-only command is a HW/build mapping check; normal scan checks source.
In Git, `python3 driver_lint/pablo_is/commit_gate.py` reads actual index bytes
and settings. Install pre-commit with `scripts/install_git.py --component pablo-is`.
Required CI status: `driver-lint-pablo-is`. Do not bypass gates, weaken policy or
reset an approved baseline to hide violations. Error/verification failures block;
review warnings, ABI impact, build/KUnit and actual HW validation separately.

Report this owner's lint result and any cross-driver changes/pending tests.

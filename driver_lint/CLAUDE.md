# Pablo IS / GDC engineering contracts

The drivers have separate developer ownership and independent lint packages:

| Driver | Contract | Scan | Active HW selection |
| --- | --- | --- | --- |
| Pablo IS | `pablo_is/CLAUDE.md` | `python3 driver_lint/pablo_is/lint.py scan` | `pablo_is/config/policy.json` → its profile |
| GDC | `gdc/CLAUDE.md` | `python3 driver_lint/gdc/lint.py scan` | `gdc/config/policy.json` → its profile |

Read the requested driver's contract and its own active profile before editing.
Pablo IS owns the camera tree excluding GDC production and GDC KUnit sources.
GDC owns `post/gdc` and matching GDC KUnit files. Kconfig, BUILD.bazel, post
Makefile and board fragments are shared files with separate registered contracts.
Common helper/API changes spanning both responsibilities need both checks.

The shared `src/driverlint` engine and `config/rules.json` implement Linux style,
safety signals, regression filtering and reports. Each owner has separate common
policy, severity overrides, exceptions, HW profiles, reference manifest and
reports. Future SoCs update that owner's HW profile/implementation/build selection
from evidence. GDC selects its own IP versions; it does not inherit Pablo family.

Run the selected owner scan before completion. Run its `scan --profile-only
--checkpatch off` after a HW/profile/build selection change. For both drivers,
`python3 driver_lint/lint.py scan` coordinates two independent checks. It no
longer uses the old combined policy by default. Explicit `--policy` remains an
advanced compatibility interface; `config/policy.json` / `profiles/s947b.json`
are retained for original reference migration and legacy regression fixtures.

Install client adapters / Git gates with `--component pablo-is|gdc|all`. Installed
pre-commit checks actual staged owner settings/build contracts/source. Server
jobs run committed BASE..HEAD with an approved runner; required statuses are
`driver-lint-pablo-is` and `driver-lint-gdc`. Local driver selection must not hide
changes belonging to the other driver from its required CI job.

Errors and verification failures block the gate. Warnings require a reviewer
explanation or narrowly scoped exception with rule/path/reason/owner/evidence.
Do not disable hooks, bypass required CI, weaken a policy or reset references to
hide failures. Preserve API/buffer/lifecycle/PM responsibilities; keep register
behavior and HW workarounds in the selected HW implementation. Keep Linux tabs,
existing helpers, names and unwind structure. Lint cannot prove compile, ABI,
IRQ/locking/DMA lifetime, runtime PM balance or HW register semantics.

Report purpose, changed layer/owner, lint command and outcome, warning disposition,
selected profile, builds, KUnit and actual HW tests performed or pending. The
source release has no root Git or usable build toolchain. Before editing a new
non-Git checkout, prepare approved source references; never initialize them after
an unreviewed edit. This checkout's owner manifests were derived from original
approved objects without resetting that original reference.

Tool maintenance: `python3 -m unittest discover -s driver_lint/tests -v`.
Open `docs/index.html` or the owner's `docs/index.html` for Korean operating details.

# Independent Pablo IS and GDC linters

The drivers have separate developer ownership and separate lint packages.
The shared Python engine implements the same Linux/style/safety checks without
copying their implementation. Each package owns its policy, HW profiles, severity
and exceptions, source reference, reports and developer instructions.

| Owner | Entry point | HW data | Korean HTML guide |
| --- | --- | --- | --- |
| Pablo IS | `pablo_is/lint.py` | `pablo_is/config/profiles/` | [Pablo IS guide](pablo_is/docs/index.html) |
| GDC | `gdc/lint.py` | `gdc/config/profiles/` | [GDC guide](gdc/docs/index.html) |

[Shared operating/architecture guide](docs/index.html) explains all 37 rules,
agent integration, real staged commit enforcement, trusted CI and their limits.
Requirements: Python 3.10+ and Perl for bundled checkpatch; no pip packages.

## Run the requested driver's linter

```bash
python3 driver_lint/pablo_is/lint.py scan
python3 driver_lint/gdc/lint.py scan

# Each owner's full registered HW/build mapping check
python3 driver_lint/pablo_is/lint.py scan --profile-only --checkpatch off
python3 driver_lint/gdc/lint.py scan --profile-only --checkpatch off

# Cross-driver changes: coordinate both independent checks
python3 driver_lint/lint.py scan
```

Pablo IS excludes `camera/post/gdc/**` and GDC KUnit files. GDC includes these
production/tests and no Pablo IS production files. Shared metadata is Kconfig,
BUILD.bazel, post/Makefile and two board fragments; each profile verifies only
its own registered contracts. Syntax/style errors in shared files can concern
both owners. Shared helper/API edits need the relevant owners' checks/reviews.

Pablo IS's S947B example selects Pablo v14_0_0 / YUVP v13_0. GDC's example selects
GDC v1400, with **no Pablo family domain or dependency on IS's profile**. Future
SoCs update the relevant owner's profile and HW/build implementation from evidence.
`policy.profile` selects that owner's default profile. `--profile NAME` is resolved
in that package's config/profiles; CLI overrides do not switch installed hooks.

Default gates block errors and verification failures; warnings require review.
Owners can promote definite style rules with their own severity_overrides.
Exit 0 pass / 1 violation / 2 inability to verify. Reports go under the owner's
`reports/`; the coordinator's combined report goes under root `driver_lint/reports/`.

## Hook and commit setup per developer

```bash
# Example: GDC developer. Use pablo-is for the other developer, all for both.
python3 driver_lint/scripts/install_claude.py --component gdc
python3 driver_lint/scripts/install_agents.py --codex --component gdc
# Optional OpenCode: select the installed runtime's API (v1 or v2)
python3 driver_lint/scripts/install_agents.py --opencode v1 --component gdc

# Each real Git checkout, after tracking tool/settings/profile/source
python3 driver_lint/scripts/install_git.py --component gdc
python3 driver_lint/gdc/commit_gate.py
```

Claude/Codex handlers and OpenCode plugin files are installed/removed independently
by component. Codex changed definitions need `/hooks` review/trust. OpenCode is
absent here; only Node callback fixtures are verified. Existing unrelated team
settings/instructions are preserved. `--remove` applies to selected adapters.
Git has one pre-commit slot: choose pablo-is, gdc or all; selecting a different
owned wrapper replaces it without chaining the previous driver's gate. Existing
team hooks still run first and are restored on removal. Local hooks can be
bypassed, so both required server jobs must own all corresponding branch changes.

## Required server CI jobs

Use the protected/base `scripts/run_ci.py` bootstrap with `--component pablo-is`
or `--component gdc`, not code submitted by the PR. Required statuses:
`driver-lint-pablo-is`, `driver-lint-gdc`. Each reads BASE..HEAD source and settings
from Git blobs and locks **only its own** common policy to an approved runner.
HW profile switches remain possible with human review. A reviewed tool maintenance
job can use a server-approved --runner-ref; PR input must not choose that value.
`integration/ci-gate.sh` accepts server-owned DRIVER_LINT_COMPONENT and
DRIVER_LINT_REVIEW_BASE. Adapt CODEOWNERS.example to the two actual teams and
protect CI/tool definitions. Multiple CODEOWNERS on one file do not themselves
require approval from both teams; use the review server's additional process
when both approvals are required for shared contracts.

The Git view assumes one repository contains settings/scoped sources/contracts.
Android repo multi-root layouts need per-repository profiles and pinned integration
CI; this installer does not combine independent indexes. This release has no root
Git, so the Git installer refuses without initializing a repository. Server
protection and live agent UI behavior remain deployment checks.

## Original reference and verification

The source release's reviewed original reference remains unchanged. Owner manifests
were derived by `scripts/split_reference.py` from its original compressed objects,
not current worktree bytes. They share the original `.state/objects` store; keep it.
A new release initializes each owner's `snapshot create` before editing approved
original source. Never reset references to hide violations. The root legacy
config/policy.json and profile are retained for original-reference migration and
advanced explicit --policy compatibility; normal CLI/hooks/CI use split packages.

```bash
python3 -m unittest discover -s driver_lint/tests -v
python3 driver_lint/scripts/build_docs.py
python3 driver_lint/scripts/verify_docs.py
```

Lint does not establish compile/ABI/IRQ/locking/PM/DMA/HW correctness. Attach builds,
KUnit and actual device evidence to behavior changes.

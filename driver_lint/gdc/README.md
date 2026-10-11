# GDC linter

Independent policy, HW profiles, source reference and reports for GDC developers.
Shared Python engine: `../src/driverlint`; shared rule definitions: `../config/rules.json`.
Scope: GDC core, selected HW API/registers, HDR10+/VOTF adapters and GDC KUnit sources. Pablo IS source and its family selection are outside this scope.

Korean HTML guide: [docs/index.html](docs/index.html).

From the workspace root:

```bash
python3 driver_lint/gdc/lint.py doctor
python3 driver_lint/gdc/lint.py scan
python3 driver_lint/gdc/lint.py scan --profile-only --checkpatch off
python3 driver_lint/gdc/lint.py scan --format html --output driver_lint/gdc/reports/change.html

# Actual Git checkout; settings/profile/source must be tracked
python3 driver_lint/scripts/install_git.py --component gdc
python3 driver_lint/gdc/commit_gate.py

# Select the client(s) used by this developer
python3 driver_lint/scripts/install_claude.py --component gdc
python3 driver_lint/scripts/install_agents.py --codex --component gdc
# OpenCode V1 example; choose v2 only for a compatible V2 runtime
python3 driver_lint/scripts/install_agents.py --opencode v1 --component gdc
```

CI: load the trusted `scripts/run_ci.py` bootstrap from protected configuration/base,
then pass `--component gdc --base <review-base>`. Required status: `driver-lint-gdc`.
The same component selector installs/removes only this owner's agent adapters.
Git has one pre-commit slot: its selector chooses one owner or both (`all`).
Codex changed definitions require `/hooks` review/trust; OpenCode live runtime is
unverified here. This release has no root Git, so the local Git hook is prepared.

In non-Git releases initialize this package's snapshot before editing. The current
`.state/reference.json` was derived from approved original objects and references
`../../.state/objects`; retain that shared store. Normal scan does not reset it.
For a new standalone baseline, use this package's `snapshot create` on approved
original source. Future projects change this package's profile from HW/build evidence.

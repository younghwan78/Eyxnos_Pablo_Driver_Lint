# Pablo IS linter

Independent policy, HW profiles, source reference and reports for Pablo IS developers.
Shared Python engine: `../src/driverlint`; shared rule definitions: `../config/rules.json`.
Scope: Pablo IS core, interface, ischain, hardware APIs/registers, sensor/CSI, libraries and non-GDC tests. GDC source/test paths are excluded.

Korean HTML guide: [docs/index.html](docs/index.html).

From the workspace root:

```bash
python3 driver_lint/pablo_is/lint.py doctor
python3 driver_lint/pablo_is/lint.py scan
python3 driver_lint/pablo_is/lint.py scan --profile-only --checkpatch off
python3 driver_lint/pablo_is/lint.py scan --format html --output driver_lint/pablo_is/reports/change.html

# Actual Git checkout; settings/profile/source must be tracked
python3 driver_lint/scripts/install_git.py --component pablo-is
python3 driver_lint/pablo_is/commit_gate.py

# Select the client(s) used by this developer
python3 driver_lint/scripts/install_claude.py --component pablo-is
python3 driver_lint/scripts/install_agents.py --codex --component pablo-is
# OpenCode V1 example; choose v2 only for a compatible V2 runtime
python3 driver_lint/scripts/install_agents.py --opencode v1 --component pablo-is
```

CI: load the trusted `scripts/run_ci.py` bootstrap from protected configuration/base,
then pass `--component pablo-is --base <review-base>`. Required status: `driver-lint-pablo-is`.
The same component selector installs/removes only this owner's agent adapters.
Git has one pre-commit slot: its selector chooses one owner or both (`all`).
Codex changed definitions require `/hooks` review/trust; OpenCode live runtime is
unverified here. This release has no root Git, so the local Git hook is prepared.

In non-Git releases initialize this package's snapshot before editing. The current
`.state/reference.json` was derived from approved original objects and references
`../../.state/objects`; retain that shared store. Normal scan does not reset it.
For a new standalone baseline, use this package's `snapshot create` on approved
original source. Future projects change this package's profile from HW/build evidence.

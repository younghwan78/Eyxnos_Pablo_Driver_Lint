#!/bin/sh
# Store this job step in protected CI configuration, or load it from the base.
# PRs must not replace the bootstrap, review-base ref, PATH or required job.
set -eu
: "${DRIVER_LINT_REVIEW_BASE:?Set a fetched, trusted destination branch ref or SHA}"
root="$(git rev-parse --show-toplevel)"
review_base="$(git rev-parse --verify --end-of-options "${DRIVER_LINT_REVIEW_BASE}^{commit}")"
base="$(git merge-base "$review_base" HEAD)"
driver_lint_component="${DRIVER_LINT_COMPONENT:-all}"
case "$driver_lint_component" in
    all) driver_lint_report="driver_lint/reports/ci-gate.json" ;;
    pablo-is) driver_lint_report="driver_lint/pablo_is/reports/ci-gate.json" ;;
    gdc) driver_lint_report="driver_lint/gdc/reports/ci-gate.json" ;;
    *) exit 2 ;;
esac
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
git show "$base:driver_lint/scripts/run_ci.py" > "$temporary/run_ci.py"
python3 -I "$temporary/run_ci.py" --root "$root" --base "$base" \
    --component "$driver_lint_component" --format json \
    --output "$driver_lint_report"

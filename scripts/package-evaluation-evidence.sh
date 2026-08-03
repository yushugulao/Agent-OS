#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TOOL="${ROOT}/host_tools/evaluation_bundle.py"
TRUSTED_PYTHON_ENTRY="${ROOT}/scripts/trusted-python-entry.py"
SUITE="${ROOT}/ci/evaluation-suite.json"

usage() {
	cat >&2 <<'EOF'
usage:
  scripts/package-evaluation-evidence.sh create <run-dir> <output-dir> [--development]
  scripts/package-evaluation-evidence.sh verify <bundle-dir>

create defaults to the formal profile and requires an evidence/releases/<bundle>
output so the append-only release index is updated atomically. --development is
deliberately explicit and produces a manifest carrying a permanent non-formal warning.
verify requires a committed formal bundle in a clean checkout.
EOF
	exit 2
}

case "${1:-}" in
create)
	[[ $# -eq 3 || ( $# -eq 4 && "$4" == "--development" ) ]] || usage
	profile=formal
	if [[ $# -eq 4 ]]; then
		profile=development
		printf 'WARNING: creating DEVELOPMENT-ONLY evidence; it is not formal competition evidence.\n' >&2
	fi
	args=(create --run-dir "$2" --suite "${SUITE}" --output "$3" \
		--contract-root "${ROOT}" --profile "${profile}")
	if [[ "${profile}" == "formal" ]]; then
		args+=(--repo-root "${ROOT}")
	fi
	exec "${PYTHON_BIN}" -I -S "${TRUSTED_PYTHON_ENTRY}" \
		"host_tools/evaluation_bundle.py" "${args[@]}"
	;;
verify)
	[[ $# -eq 2 ]] || usage
	exec "${PYTHON_BIN}" -I -S "${TRUSTED_PYTHON_ENTRY}" \
		"host_tools/evaluation_bundle.py" verify --bundle "$2" \
		--contract-root "${ROOT}" --repo-root "${ROOT}" --require-committed
	;;
*)
	usage
	;;
esac

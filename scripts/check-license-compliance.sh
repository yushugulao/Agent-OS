#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only

set -euo pipefail

require_file() {
	local path="$1"
	if [[ ! -f "$path" ]]; then
		echo "missing required file: $path" >&2
		exit 1
	fi
}

require_text() {
	local path="$1"
	local pattern="$2"
	if ! grep -Eq "$pattern" "$path"; then
		echo "missing required text in $path: $pattern" >&2
		exit 1
	fi
}

require_file LICENSE
require_file DOCUMENTATION_LICENSE.md
require_file NOTICE
require_file LICENSE_POLICY.md
require_file README.md

require_text LICENSE "GNU GENERAL PUBLIC LICENSE"
require_text DOCUMENTATION_LICENSE.md "SPDX-License-Identifier: CC-BY-SA-4.0"
require_text DOCUMENTATION_LICENSE.md "creativecommons.org/licenses/by-sa/4.0/legalcode"
require_text NOTICE "Source code license: .*GPL-3.0"
require_text NOTICE "Technical documents.*CC BY-SA 4.0"
require_text NOTICE "RustSBI"
require_text NOTICE "Mulan PSL v2"
require_text LICENSE_POLICY.md "source code must use at least one of GPL, Apache, BSD, or Mulan"
require_text LICENSE_POLICY.md "technical documents and defense materials must use CC BY-SA 4.0"
require_text README.md "源代码.*GPL-3.0"
require_text README.md "CC BY-SA 4.0|Creative Commons Attribution-ShareAlike 4.0"

if grep -RInE "CC-BY-NC|CC BY-NC|NonCommercial|NoDerivatives|proprietary|confidential|All rights reserved" \
	README.md docs DOCUMENTATION_LICENSE.md NOTICE >/tmp/agentos-license-forbidden.log; then
	cat /tmp/agentos-license-forbidden.log >&2
	echo "found incompatible documentation license wording" >&2
	exit 1
fi

sensitive_pattern="$(printf '%s|%s|%s|%s' \
	'gl''pat-' \
	'oauth''2:' \
	'Authorization: ''Basic' \
	'tokens''\.txt')"

if grep -RInIE \
	--exclude-dir=.git \
	--exclude-dir=build \
	--exclude-dir=target \
	--exclude-dir=asm \
	--exclude='*.img' \
	--exclude='fs' \
	"$sensitive_pattern" . >/tmp/agentos-license-sensitive.log; then
	cat /tmp/agentos-license-sensitive.log >&2
	echo "found sensitive credential marker" >&2
	exit 1
fi

echo "license compliance: ok"

#!/bin/bash
# Builds dist/psmphealth-<version>.tar.gz (code, docs, example environment - no site data, logs or secrets) plus a SHA256SUMS file; GPG-signs when PSMP_GPG_KEY is set.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"; v=$(cat VERSION); out=dist; mkdir -p "$out"
python3 tests/selftest.py >/dev/null 2>&1 || { echo "self-test failed - not releasing"; exit 1; }
tar --exclude='__pycache__' -czf "$out/psmphealth-$v.tar.gz" README.md VERSION psmp-engine.sh psmp-ssh.py run-psmp-tests.py make-release.sh lib docs contrib tests envs/example
( cd "$out" && sha256sum "psmphealth-$v.tar.gz" > "psmphealth-$v.SHA256SUMS" && cat "psmphealth-$v.SHA256SUMS" )
[ -z "${PSMP_GPG_KEY:-}" ] || gpg --local-user "$PSMP_GPG_KEY" --detach-sign --armor "$out/psmphealth-$v.tar.gz"
echo "release written to $out/"

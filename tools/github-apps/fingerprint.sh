#!/usr/bin/env bash
# Print a GitHub App private key's SHA256 fingerprint, the same value GitHub shows
# under "Private keys" on the app's settings page. Optionally compare it.
#
# Usage: fingerprint.sh <key.pem> [expected]     expected may carry the SHA256: prefix
#
# Only the public half is hashed; the key itself is never printed.
set -euo pipefail
pem="${1:?usage: fingerprint.sh <key.pem> [expected]}"
expected="${2:-}"
fp="$(openssl rsa -in "$pem" -pubout -outform DER 2>/dev/null | openssl dgst -sha256 -binary | openssl base64 -A)"
[ -n "$fp" ] || { echo "could not read a key from $pem" >&2; exit 2; }
echo "SHA256:$fp"
if [ -n "$expected" ]; then
  if [ "${expected#SHA256:}" = "$fp" ]; then echo "match"; else echo "MISMATCH (settings page shows ${expected#SHA256:})" >&2; exit 3; fi
fi

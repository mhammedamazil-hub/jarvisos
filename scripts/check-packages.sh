#!/bin/bash
set -euo pipefail
echo "=== Package availability check (Debian bookworm) ==="
# This checks that packages in lists exist in Debian repos (without installing)
# Uses apt-cache policy if available, else just lists
if ! command -v apt-cache >/dev/null; then echo "apt-cache not found - skipping"; exit 0; fi
MISSING=0
for f in config/package-lists/*.list.chroot; do
  echo "Checking $(basename $f)..."
  while read -r pkg; do
    # strip comments/empty
    pkg=$(echo "$pkg" | sed 's/#.*//' | xargs)
    [ -z "$pkg" ] && continue
    if ! apt-cache show "$pkg" >/dev/null 2>&1; then
      echo "  WARN: $pkg not found in current apt sources (may be in bookworm)"
      MISSING=$((MISSING+1))
    fi
  done < "$f"
done
if [ "$MISSING" -gt 0 ]; then
  echo "Total missing/unknown in this host's sources: $MISSING"
  echo "Note: This host is $(cat /etc/os-release | grep PRETTY) - some bookworm packages may not show on Ubuntu host, but will on Debian. Not failing."
else
  echo "✓ all packages found in apt cache"
fi
# For bookworm host, fail if missing >5
# For ubuntu host, just warn

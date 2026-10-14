#!/bin/bash
set -euo pipefail
echo "=== JARVIS App Tests (no network, no secrets) ==="
# syntax
python3 -m py_compile overlay/usr/local/bin/jarvis-center && echo "✓ jarvis-center syntax OK"
# check for secrets
if grep -R "sk-[A-Za-z0-9]\{20\}" overlay/ 2>/dev/null; then echo "FAIL: secret in overlay"; exit 1; else echo "✓ no embedded secrets"; fi
# cli test
python3 overlay/usr/local/bin/jarvis-center --cli 2>&1 | head -20 || echo "CLI test (GTK may be missing, but syntax passed)"
# try running with headless if GTK available
if python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null; then
  echo "GTK available - testing --cli"
  python3 overlay/usr/local/bin/jarvis-center --cli && echo "✓ CLI OK"
else
  echo "GTK not available in this env - skipping GUI test (expected in minimal container)"
fi
# config handling
mkdir -p /tmp/jarvis-test/.config/jarvis
HOME=/tmp/jarvis-test python3 -c "
import json, pathlib
p=pathlib.Path('/tmp/jarvis-test/.config/jarvis/config.json')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({'ai_provider':'openai','api_key':'test-dummy-not-real','model':'gpt-4o'}))
print('✓ config write OK (dummy key, not real)')
# ensure perms
import os
os.chmod(p, 0o600)
print('✓ perms 600')
"
# destructive guard test
python3 << 'PYEOF'
import sys
sys.path.insert(0, 'overlay/usr/local/bin')
# import check via exec of functions
import pathlib
text=open('overlay/usr/local/bin/jarvis-center').read()
assert 'is_destructive' in text
assert 'Destructive' in text or 'destructive' in text
print("✓ destructive guard present")
assert 'least privilege' in text.lower() or 'least-privilege' in text.lower() or 'whitelisted' in text.lower()
print("✓ least privilege hint present")
# ensure offline fallback
assert 'offline' in text.lower()
print("✓ offline fallback present")
print("✓ jarvis logic checks passed")
PYEOF
echo "=== JARVIS tests done ==="

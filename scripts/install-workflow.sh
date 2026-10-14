#!/bin/bash
set -e
echo "=== Install GitHub Actions workflow (needs workflows permission) ==="
echo "The workflow file is at .github/workflows/build-jarvisos.yml locally but GitHub Apps cannot push it."
echo "You (owner) must create it via the GitHub web UI:"
echo ""
echo "1. On https://github.com/mhammedamazil-hub/jarvisos go to Add file -> Create new file"
echo "2. Path: .github/workflows/build-jarvisos.yml"
echo "3. Copy-paste the contents of docs/workflow-build-jarvisos.yml (or .github/workflows/build-jarvisos.yml locally)"
echo "4. Commit directly to arena/5a9160ac-jarvisos (or main)"
echo "5. Then Actions -> Build JARVISOS -> Run workflow will appear"
echo ""
echo "Alternatively, if you have a PAT with workflow scope:"
echo "  gh auth login  # with PAT"
echo "  git add .github/workflows/build-jarvisos.yml"
echo "  git commit -m 'ci: add workflow' && git push origin arena/5a9160ac-jarvisos"
echo ""
echo "Local workflow file exists at:"
ls -lh .github/workflows/build-jarvisos.yml docs/workflow-build-jarvisos.yml 2>&1

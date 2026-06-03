#!/usr/bin/env bash
# install.sh — instalace skillu ares-dph-isir pro Claude Code
#
# Použití:
#   bash install.sh
#   curl -fsSL https://raw.githubusercontent.com/semeradoval/ares-dph-isir/main/install.sh | bash

set -euo pipefail

REPO_URL="https://github.com/semeradoval/ares-dph-isir.git"
SKILL_DIR="${HOME}/.claude/skills/ares-dph-isir"

echo "→ Instalace skillu ares-dph-isir"

# Klonování nebo aktualizace
if [ -d "${SKILL_DIR}/.git" ]; then
  echo "→ Aktualizace existující instalace..."
  git -C "${SKILL_DIR}" pull --ff-only
else
  echo "→ Klonování do ${SKILL_DIR}..."
  mkdir -p "$(dirname "${SKILL_DIR}")"
  git clone "${REPO_URL}" "${SKILL_DIR}"
fi

# Python závislosti
if command -v pip3 &>/dev/null; then
  echo "→ Instalace Python závislostí..."
  pip3 install -q -r "${SKILL_DIR}/scripts/requirements.txt"
elif command -v pip &>/dev/null; then
  pip install -q -r "${SKILL_DIR}/scripts/requirements.txt"
else
  echo "⚠️  pip nenalezen — nainstaluj závislosti ručně:"
  echo "   pip install -r ${SKILL_DIR}/scripts/requirements.txt"
fi

echo ""
echo "✓ Hotovo. Restartuj Claude Code a použij skill:"
echo "  /ares-dph-isir <IČO nebo název firmy>"

#!/usr/bin/env bash
# =============================================================================
# push.sh — Stage, commit, and push all changes to GitHub
#
# Usage:
#   ./scripts/push.sh "your commit message"
#   ./scripts/push.sh                        # uses a default message
#
# Optional flags:
#   --regen [count] [seed]   Regenerate transactions.json before committing
#                            (default count=550, seed=42)
#   --pipeline               Re-run the fraud pipeline and update fraud_detection.db
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve project root regardless of where the script is called from
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
MSG=""
REGEN=false
REGEN_COUNT=550
REGEN_SEED=42
RUN_PIPELINE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --regen)
      REGEN=true
      shift
      if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then REGEN_COUNT="$1"; shift; fi
      if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then REGEN_SEED="$1";  shift; fi
      ;;
    --pipeline)
      RUN_PIPELINE=true
      shift
      ;;
    *)
      MSG="$1"
      shift
      ;;
  esac
done

[[ -z "$MSG" ]] && MSG="chore: update project files ($(date '+%Y-%m-%d %H:%M'))"

# ---------------------------------------------------------------------------
# Optional: regenerate test data
# ---------------------------------------------------------------------------
if [[ "$REGEN" == true ]]; then
  echo "→ Regenerating $REGEN_COUNT transactions (seed=$REGEN_SEED)..."
  if [[ -f ".venv/bin/python" ]]; then
    .venv/bin/python data/generate_data.py --count "$REGEN_COUNT" --seed "$REGEN_SEED"
  else
    python3 data/generate_data.py --count "$REGEN_COUNT" --seed "$REGEN_SEED"
  fi
fi

# ---------------------------------------------------------------------------
# Optional: re-run the fraud pipeline (updates fraud_detection.db)
# ---------------------------------------------------------------------------
if [[ "$RUN_PIPELINE" == true ]]; then
  echo "→ Running fraud detection pipeline..."
  if [[ -f ".venv/bin/python" ]]; then
    .venv/bin/python scripts/run_pipeline.py --delay 0
  else
    python3 scripts/run_pipeline.py --delay 0
  fi
fi

# ---------------------------------------------------------------------------
# Git: stage → commit → push
# ---------------------------------------------------------------------------
echo ""
echo "=== Pushing to GitHub ==="
echo "  Message : $MSG"
echo "  Branch  : $(git rev-parse --abbrev-ref HEAD)"
echo ""

git add -A

# Nothing to commit? Exit cleanly.
if git diff --cached --quiet; then
  echo "✓ Nothing to commit — working tree clean."
  exit 0
fi

git commit -m "$MSG

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

git push origin "$(git rev-parse --abbrev-ref HEAD)"

echo ""
echo "✓ Pushed → https://github.com/davdmonroy/ChallengeFounders"

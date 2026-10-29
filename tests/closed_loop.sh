#!/usr/bin/env bash
# Closed-loop reproducibility test: produce a release, then reproduce its result
# from the bundle *alone* (no original repo, remote, or cache) and assert the
# reproduced metric matches the produced one within tolerance.
#
# This is the executable version of the round trip documented in docs/HOWTO.md
# (produce) and docs/REPRODUCING.md (reproduce). It is slow and needs network
# (it runs `uv sync` twice), so it is NOT part of the fast `pytest` suite — run
# it deliberately:  bash tests/closed_loop.sh
#
# It exercises the worked example (synthetic data). Step 3's data acquisition is
# therefore the embedded-bytes path, not a real external download — see
# REPRODUCING.md. Exits non-zero on any failure or metric divergence.
set -euo pipefail

TOL="${TOL:-0.02}"                 # metric equivalence tolerance
HERE="$(cd "$(dirname "$0")/.." && pwd)"   # project root
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
echo "closed-loop test in $WORK (tolerance $TOL)"

metric() { python3 -c "import json,sys
with open(sys.argv[1]) as f: print(json.load(f)['accuracy'])" "$1"; }

# ---------------------------------------------------------------------------
echo "== PRODUCER: run the pipeline and build a release =="
P="$WORK/producer"
mkdir -p "$P" && cp -r "$HERE/." "$P/caster/" 2>/dev/null || true
cd "$P/caster"
rm -rf .git .venv uv.lock outputs releases models data/raw/synthetic \
       data/processed/synthetic data/features .dvc dvc.lock 2>/dev/null || true
git init -q && git config user.email t@t.t && git config user.name t
uv sync --extra dev --extra packaging >/dev/null 2>&1
uv run dvc init -q >/dev/null 2>&1
git add -A && git commit -qm init >/dev/null
uv run dvc repro >/dev/null 2>&1
PROD="$(metric models/validation_metrics.json)"
echo "  produced validation accuracy = $PROD"
uv run dvc commit -f >/dev/null 2>&1
git add -A && git commit -qm run >/dev/null
RUN_DIR="$(find outputs -name model.skops | head -1 | xargs dirname)"
REL="$WORK/release.yaml"
sed "s|run_dir: outputs/2026-01-01/00-00-00|run_dir: $RUN_DIR|g; \
     s|eval_dir: outputs/2026-01-01/00-00-00|eval_dir: $RUN_DIR|g" \
     configs/release/v1.0_main.yaml > "$REL"
uv run caster package "$REL" >/dev/null 2>&1
BUNDLE="$(ls releases/*/*.zip | head -1)"
cp "$BUNDLE" "$WORK/release.zip"
echo "  bundle: $(basename "$BUNDLE")"

# ---------------------------------------------------------------------------
echo "== REPRODUCER: reproduce from the bundle alone =="
R="$WORK/reproducer"
mkdir -p "$R" && cd "$R"
unzip -q "$WORK/release.zip"
BD="$(ls -d */ | head -1)"; BD="${BD%/}"
# 1. intact
test -f "$BD/manifest-sha256.txt" && echo "  [1] bundle manifest present"
# 3. recreate env from the source tarball
tar xf "$BD"/data/source/*.tar.gz -C "$R"
SRC="$(find "$R" -name pyproject.toml -path '*/[!.]*' | head -1 | xargs dirname)"
cd "$SRC"
git init -q && git config user.email t@t.t && git config user.name t
uv sync --extra dev --extra packaging >/dev/null 2>&1
# the source archive already carries .dvc/ (committed by the producer); only
# initialize if it somehow doesn't.
[ -d .dvc ] || uv run dvc init -q >/dev/null 2>&1
echo "  [3] environment recreated from source/"
# 4. restore embedded data; do NOT let fetch re-acquire
mkdir -p data/raw/synthetic
cp -r "$R/$BD"/data/datasets/synthetic/* data/raw/synthetic/
git add -A && git commit -qm init >/dev/null
uv run dvc commit fetch -f >/dev/null 2>&1
if uv run dvc repro 2>&1 | grep -q "Running stage 'fetch'"; then
  echo "  [4] ERROR: fetch re-ran — bundled data was regenerated, not reproduced" >&2
  exit 1
fi
echo "  [4] fetch skipped; pipeline ran on the bundled data"
# 5. equivalence
REPRO="$(metric models/validation_metrics.json)"
echo "  [5] produced=$PROD  reproduced=$REPRO"
python3 - "$PROD" "$REPRO" "$TOL" <<'PY'
import sys
prod, repro, tol = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
if abs(prod - repro) <= tol:
    print(f"\nCLOSED LOOP: PASS — reproduced within {tol} (|{prod}-{repro}|)")
    sys.exit(0)
print(f"\nCLOSED LOOP: FAIL — diverged ({prod} vs {repro}, tol {tol})", file=sys.stderr)
sys.exit(1)
PY

#! /usr/bin/env bash
# Filename:    run.sh
# Description: Single gate for the Levoit Purifier tests. Exit 0 only if
#              everything passes.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026
# Version:     1.0.0
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."
PY="${PYTHON:-python3}"
SRC="Levoit.indigoPlugin/Contents/Server Plugin"

echo "== pytest (logic, VeSync client, plugin, XML contract, secrets) =="
"$PY" -m pytest tests -q

echo
echo "== python syntax =="
# -B: no __pycache__ written inside the shipped bundle.
PYTHONDONTWRITEBYTECODE=1 "$PY" -B -m py_compile "$SRC/plugin.py" "$SRC/levoit_logic.py" \
                      "$SRC/vesync_client.py" "$SRC/plugin_utils.py"
echo "  ok"

echo
echo "== XML well-formed =="
for f in "$SRC"/*.xml "Levoit.indigoPlugin/Contents/Info.plist"; do
    "$PY" -c "import xml.dom.minidom,sys; xml.dom.minidom.parse(sys.argv[1])" "$f"
    echo "  ok  $(basename "$f")"
done

echo
echo "== lint (errors only) =="
# NOT `ruff check . && echo ok` - `set -e` does not fire on the left-hand side
# of `&&`, so that form lets a lint failure through and the gate still exits 0.
"$PY" -m ruff check .
echo "  ok"

echo
find Levoit.indigoPlugin -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "All Levoit Purifier tests passed."

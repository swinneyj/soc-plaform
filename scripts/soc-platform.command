#!/bin/bash
# SOC Platform double-click launcher (macOS equivalent of the Windows
# Start_SOC_Platform.bat). Double-clicking this file in Finder:
#   1. starts every platform piece (Ollama, secrets, API) via scripts/start
#   2. opens the dashboard in the default browser
#   3. leaves a Terminal window tailing the API log
#      (Ctrl+C detaches the log view; the API keeps running)
#
# Install: copy to the Desktop and double-click. The repo root is resolved
# relative to this file's own location, so it works from any checkout path.
# First run only: if Gatekeeper asks, right-click -> Open once.

# Resolve the repo root: when this file lives inside the repo (scripts/ or
# repo root), use its location; when copied to the Desktop, fall back to the
# documented checkout paths.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if   [ -f "$HERE/scripts/start" ]; then ROOT="$HERE"          # launcher at repo root
elif [ -f "$HERE/../scripts/start" ]; then ROOT="$(cd "$HERE/.." && pwd)"  # launcher in scripts/
elif [ -f "$HOME/Downloads/soc-plaform-main/scripts/start" ]; then
    ROOT="$HOME/Downloads/soc-plaform-main"                     # Desktop copy
elif [ -f "$HOME/Documents/soc-plaform/scripts/start" ]; then
    ROOT="$HOME/Documents/soc-plaform"
else
    echo "✗ Could not locate the SOC Platform checkout (scripts/start not found)." >&2
    echo "  Keep this launcher inside the repo, or edit ROOT below." >&2
    read -n 1 -s -r -p "Press any key to close…"
    exit 1
fi

cd "$ROOT" || exit 1
bash scripts/start
open "http://127.0.0.1:8000/index.modular.html"

echo ""
echo "API log (Ctrl+C detaches; the API keeps running):"
exec tail -f /tmp/soc-api.log

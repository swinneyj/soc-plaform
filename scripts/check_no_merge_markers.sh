#!/usr/bin/env bash
set -euo pipefail
# Fail if any unresolved git merge markers remain in tracked files.
# Mirrors frontend cutover guard (web/index.html had 3 blocks).
# Use real merge markers only: ^<<<<<<< + space (not ===== dividers in comments)
# Exclude archives and generated tool_packager dividers
if grep -rn -E "^<<<<<<< |^>>>>>>> " --include="*.py" --include="*.js" --include="*.html" --include="*.ps1" --include="*.md" --include="*.json" . 2>/dev/null | grep -v "_archive_oneoffs" | grep -v "Old_archive" | grep -v "/.git/" | grep -v "tool_packager" ; then
  echo "ERROR: unresolved merge markers found above"
  exit 1
fi
echo "OK: no merge markers"

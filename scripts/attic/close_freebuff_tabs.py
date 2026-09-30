#!/usr/bin/env python3
"""
close_freebuff_tabs.py — close every browser tab EXCEPT ones matching a keep pattern.

Default keep pattern: "freebuff" (case-insensitive). Browsers that are NOT
running are never touched or launched — only open apps are affected.

Usage (in Terminal):
  python3 close_freebuff_tabs.py                  # close all tabs except "freebuff"
  python3 close_freebuff_tabs.py freebuff github  # keep any of several patterns
  python3 close_freebuff_tabs.py --dry-run        # preview what would close; closes nothing

First run: macOS will ask permission for your terminal to control
Safari/Chrome — click OK. (Change later in System Settings >
Privacy & Security > Automation.)
"""

import subprocess
import sys

# Browsers to check, by application name. Add yours to the right list if missing.
SAFARI_LIKE = ["Safari"]
CHROME_LIKE = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Arc", "Chromium", "Vivaldi"]


def osascript(script):
    """Run an AppleScript and return its output."""
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    return result.stdout.strip()


def is_running(app_name):
    """True only if the app is currently running (never launches anything)."""
    try:
        return osascript('application "%s" is running' % app_name).lower() == "true"
    except Exception:
        return False


def asl(text):
    """Quote a string as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def keep_condition(app_name, patterns):
    """AppleScript condition that is TRUE for tabs we want to CLOSE."""
    kw = "whose" if app_name in CHROME_LIKE else "where"
    parts = " and ".join("URL does not contain %s" % asl(p) for p in patterns)
    return "%s %s" % (kw, parts)


def close_tabs(app_name, patterns):
    script = 'tell application "%s" to close (every tab of every window %s)' % (
        app_name,
        keep_condition(app_name, patterns),
    )
    osascript(script)


def list_tab_urls(app_name):
    """Return all open tab URLs (best effort) — used by --dry-run."""
    script = (
        'tell application "%s"\n'
        '    set out to ""\n'
        "    repeat with w in windows\n"
        "        repeat with t in tabs\n"
        "            try\n"
        "                set out to out & (URL of t) & linefeed\n"
        "            end try\n"
        "        end repeat\n"
        "    end repeat\n"
        "    return out\n"
        "end tell" % app_name
    )
    out = osascript(script)
    return [line for line in out.splitlines() if line.strip()]


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    patterns = [a for a in args if a != "--dry-run"] or ["freebuff"]

    mode = "DRY RUN — nothing will be closed" if dry_run else "LIVE — closing tabs"
    print("Keep pattern(s): %s   [%s]" % (", ".join(patterns), mode))

    touched = False
    for app in SAFARI_LIKE + CHROME_LIKE:
        if not is_running(app):
            continue  # skip browsers that aren't open
        touched = True
        try:
            if dry_run:
                urls = list_tab_urls(app)
                doomed = [
                    u for u in urls
                    if not any(p.lower() in u.lower() for p in patterns)
                ]
                print(
                    "\n[%s] %d tab(s): would close %d, keep %d"
                    % (app, len(urls), len(doomed), len(urls) - len(doomed))
                )
                for u in doomed:
                    print("   CLOSE: " + u[:100])
            else:
                close_tabs(app, patterns)
                print("[%s] done." % app)
        except Exception as exc:
            print("[%s] error: %s" % (app, exc))

    if not touched:
        print("\nNo supported browser is currently running — nothing to do.")


if __name__ == "__main__":
    main()

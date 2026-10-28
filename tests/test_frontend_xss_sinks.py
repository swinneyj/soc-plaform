"""
Frontend XSS-sink canary (API security review, optimization #6).

The web/ UI renders server-controlled text everywhere: error `detail`
payloads (F1 puts str(exc) into 500 responses), AI analysis output,
evidence summaries, artifact paths. Any HTML sink fed server text is an
XSS vector — in a SOC UI the feed includes attacker-influenced notable
fields, so this is escalation-class.

This canary pins the audited state as tests:

1. No raw HTML-injection sinks anywhere in web/ (innerHTML,
   insertAdjacentHTML, outerHTML, document.write, eval, new Function,
   srcdoc, javascript: URLs). Error detail must keep flowing only through
   alert()/console/Vue text interpolation.
2. `v-html` (the Vue equivalent of innerHTML) is allowed only in
   web/components/AnalysisTab.js, fed exclusively by formatAnalysisText.
3. That formatter's escapeHtml must escape & < > " ' — with & first —
   before emitting any of its own tags. Removing an escape or reordering
   fails CI.

Runs in the standard pytest suite on every push; no JS toolchain needed.
Update the allowlists consciously, never silently.
"""

import re
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# (pattern, reason) — each match is a finding a human must review.
FORBIDDEN_SINKS = [
    (r"\binnerHTML\b", "direct HTML injection sink"),
    (r"\binsertAdjacentHTML\b", "direct HTML injection sink"),
    (r"\bouterHTML\b", "direct HTML injection sink (write form)"),
    (r"\bdocument\.write\b", "direct HTML injection sink"),
    (r"\beval\s*\(", "code execution from strings"),
    (r"\bnew\s+Function\s*\(", "code execution from strings"),
    (r"\bsrcdoc\b", "inline-frame HTML injection"),
    (r"javascript\s*:", "script URL (attribute injection)"),
]

# The single sanctioned v-html: renders formatAnalysisText(...) output,
# which escapes before formatting (see the escaper test below).
V_HTML_ALLOWED_FILES = {"components/AnalysisTab.js"}


def _web_sources():
    """Yield (posix path relative to web/, file text) for every JS/HTML source."""
    for path in sorted(WEB_DIR.rglob("*")):
        if path.is_file() and path.suffix in {".js", ".html"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            yield path.relative_to(WEB_DIR).as_posix(), text


def test_no_html_injection_sinks_in_web_sources():
    violations = []
    for rel, text in _web_sources():
        for pattern, why in FORBIDDEN_SINKS:
            for match in re.finditer(pattern, text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(f"web/{rel}:{line}: {why} (/{pattern}/)")
    assert not violations, "HTML-injection sinks found in web/:\n" + "\n".join(violations)


def test_v_html_only_in_sanctioned_file():
    violations = []
    for rel, text in _web_sources():
        if rel in V_HTML_ALLOWED_FILES:
            continue
        for match in re.finditer(r"\bv-html\b", text):
            line = text.count("\n", 0, match.start()) + 1
            violations.append(f"web/{rel}:{line}: v-html outside the sanctioned formatter")
    assert not violations, "v-html appeared outside AnalysisTab.js:\n" + "\n".join(violations)


def test_sanctioned_v_html_escaper_covers_all_html_specials_and_ampersand_first():
    """formatAnalysisText must escape & < > " ' before emitting its own tags,
    with the & rule first (running it later would double-encode the others)."""
    source = (WEB_DIR / "components" / "AnalysisTab.js").read_text(encoding="utf-8")
    start = source.find("const escapeHtml")
    assert start != -1, "escapeHtml helper vanished from AnalysisTab.js"
    end = source.find("const inline", start)
    assert end != -1, "escapeHtml block boundary (const inline) not found"
    block = source[start:end]

    # Regexes tolerate cosmetic differences (e.g. a redundant \ before "
    # inside the regex literal, whitespace around the comma).
    required = [
        (r"\.replace\(/&/g,\s*'&amp;'\)", "ampersand"),
        (r"\.replace\(/</g,\s*'&lt;'\)", "less-than"),
        (r"\.replace\(/>/g,\s*'&gt;'\)", "greater-than"),
        (r"\.replace\(/\\?\"/g,\s*'&quot;'\)", "double-quote"),
        (r"\.replace\(/'/g,\s*'&#39;'\)", "single-quote"),
    ]
    missing = [what for pattern, what in required if not re.search(pattern, block)]
    assert not missing, (
        "escapeHtml no longer escapes (re-add, updating this test only with "
        f"a written rationale): {', '.join(missing)}"
    )

    ampersand_at = re.search(required[0][0], block).start()
    reordered = [
        what
        for pattern, what in required[1:]
        if re.search(pattern, block).start() < ampersand_at
    ]
    assert not reordered, (
        "escapeHtml must run the & rule before: " + ", ".join(reordered)
    )

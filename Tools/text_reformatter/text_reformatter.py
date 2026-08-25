# TOOL_NAME: text_reformatter
# DESC: Reflows garbled text with hard-wrapped lines into cleaner paragraphs for AI analysis.
# CATEGORY: System Utilities
# ARG: --target | Target Text File | Path to the text file to reflow | True
# ARG: --width | Line Width | Desired wrap width (default 100) | False

import os
import argparse
import textwrap


def load_text(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def save_text(path: str, content: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def reflow_text(raw: str, width: int) -> str:
    """Reflow text that has hard line breaks into paragraphs.

    Heuristics:
    - Blank lines are treated as paragraph boundaries.
    - Within a paragraph, lines are joined with spaces and then wrapped.
    - Bullet/list lines (starting with '-', '*', or numbered lists like '1.') are kept as separate paragraphs.
    """
    lines = raw.splitlines()

    paragraphs = []
    current = []

    def flush_current():
        nonlocal current
        if current:
            paragraphs.append(" ".join(current))
            current = []

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            # Blank line -> paragraph break
            flush_current()
            paragraphs.append("")
            continue

        # Treat bullet / numbered lines as standalone paragraphs
        stripped_l = stripped.lstrip()
        if stripped_l.startswith(('-', '*')) or stripped_l[:2].isdigit() and stripped_l[2:3] in ['.', ')']:
            flush_current()
            paragraphs.append(stripped)
            continue

        current.append(stripped_l)

    flush_current()

    # Wrap paragraphs
    wrapped_paragraphs = []
    for p in paragraphs:
        if not p:
            wrapped_paragraphs.append("")
            continue
        # Do not wrap very short lines
        if len(p) <= width:
            wrapped_paragraphs.append(p)
            continue
        wrapped_paragraphs.append(textwrap.fill(p, width=width))

    return "\n".join(wrapped_paragraphs)


def main():
    parser = argparse.ArgumentParser(description="Text Reformatter")
    parser.add_argument('--target', type=str, required=True, help="Path to the text file to reflow")
    parser.add_argument('--width', type=int, default=100, help="Desired wrap width (default 100)")
    parser.add_argument('--silent', action='store_true', help="Suppress output messages")
    args = parser.parse_args()

    target = args.target.strip('"').strip("'")
    if not os.path.exists(target):
        if not args.silent:
            print(f"[!] File not found: {target}")
        return

    raw = load_text(target)
    reflowed = reflow_text(raw, args.width)

    base_dir = os.path.dirname(target)
    name = os.path.basename(target)
    out_name = f"Reflowed_{name}"
    out_path = os.path.join(base_dir, out_name)

    save_text(out_path, reflowed)

    if not args.silent:
        print(f"[+] Reflow complete. Saved to: {out_path}")

    # No Commander prompt handling here to keep the tool simple; Commander/Playbook_Runner can wrap it.


if __name__ == "__main__":
    main()

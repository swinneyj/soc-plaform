# TOOL_NAME: Splunk_Notable_AI_Helper
# DESC: Guided helper that takes a sanitized Splunk ES Incident Review notable export and builds an AI prompt asking for SPL, disposition, and a two-sentence closing comment.
# CATEGORY: Reporting
# ARG: --evidence | Evidence File | Path to the sanitized notable text or export (optional; will prompt if omitted) | False

import os
import sys
import argparse
import datetime

# Ensure both the Tools directory and this folder are on sys.path so we can
# import core_lib and the ai_analyst_engine module.
current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
for p in (tools_dir, current_dir):
    if p not in sys.path:
        sys.path.append(p)

from core_lib.utils import Colors, clear_screen, get_platform_root

# We reuse the generate_prompt helper from ai_analyst_engine.py so that all
# prompts land in Data/Active_Workspace with the same naming convention.
try:
    from ai_analyst_engine import generate_prompt
except Exception:
    # Fallback: allow this script to be used even if the import fails.
    generate_prompt = None


def resolve_skill_path(platform_root: str) -> str:
    """Return the path to an environment-neutral Splunk notable investigation SKILL.

    Preference order:
    1) splunk-notable-investigation-public_SKILL.md (public-safe, environment-neutral)
    2) splunk-notable-investigation_SKILL.md (full internal version)
    """
    kb_dir = os.path.join(platform_root, 'Data', 'AI_Knowledge_Base')
    public_skill = os.path.join(kb_dir, 'splunk-notable-investigation-public_SKILL.md')
    if os.path.exists(public_skill):
        return public_skill

    # Fallback: use the full internal skill if the public-safe variant
    # is not present. This keeps existing workflows working while
    # encouraging the environment-neutral SKILL for public LLM use.
    generic_skill = os.path.join(kb_dir, 'splunk-notable-investigation_SKILL.md')
    if os.path.exists(generic_skill):
        return generic_skill

    raise FileNotFoundError(f"No Splunk notable investigation SKILL found in {kb_dir}.")


def prompt_for_evidence_file(platform_root: str) -> str:
    """Interactive prompt for the evidence file path.

    This is intended for analysts who have already sanitized and saved
    the Incident Review notable/export to disk (TXT/MD/CSV).
    """
    clear_screen()
    print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
    print(" SPLUNK NOTABLE AI HELPER ".center(80))
    print("=" * 80 + f"{Colors.ENDC}")

    print(f"\n{Colors.HEADER}Purpose:{Colors.ENDC} Build an AI prompt for any Splunk ES Incident Review notable so an IL5 LLM can:\n"
          f"- Propose investigation SPL queries,\n"
          f"- Recommend a disposition (Status + Disposition label), and\n"
          f"- Generate a concise two-sentence closing comment.")

    print(f"\n{Colors.CYAN}TIP:{Colors.ENDC} Point this at a sanitized export or paste output saved from Incident Review (TXT, MD, or CSV).\n")

    while True:
        path = input(f"{Colors.BLUE}Evidence file path (or 'back' to cancel):{Colors.ENDC} ").strip()
        if not path:
            continue
        if path.lower() in ('back', 'exit', '0', 'cancel'):
            return ''

        # Allow relative paths from platform root for convenience
        candidate = path.strip('"').strip("'")
        if not os.path.isabs(candidate):
            candidate_abs = os.path.join(platform_root, candidate)
        else:
            candidate_abs = candidate

        if os.path.exists(candidate_abs):
            return candidate_abs

        print(f"{Colors.FAIL}[!] File not found: {candidate_abs}{Colors.ENDC}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Splunk Notable AI Helper")
    parser.add_argument('--evidence', type=str, required=False,
                        help="Path to the sanitized notable text/export (TXT/MD/CSV)")
    parser.add_argument('--silent', action='store_true', help="Suppress UI output")
    args = parser.parse_args()

    platform_root = get_platform_root()
    output_dir = os.path.join(platform_root, 'Data', 'Active_Workspace')
    os.makedirs(output_dir, exist_ok=True)

    if generate_prompt is None:
        if not args.silent:
            print(f"{Colors.FAIL}[!] Unable to import AI_Analyst_Engine.generate_prompt.\n"
                  f"    Ensure ai_analyst_engine.py is present and accessible from Tools/ai_analyst_engine.{Colors.ENDC}")
        return

    # Resolve evidence path (CLI or interactive)
    evidence_path = args.evidence.strip('"').strip("'") if args.evidence else ''
    if evidence_path:
        if not os.path.isabs(evidence_path):
            candidate = os.path.join(platform_root, evidence_path)
            if os.path.exists(candidate):
                evidence_path = candidate
        if not os.path.exists(evidence_path):
            if not args.silent:
                print(f"{Colors.FAIL}[!] Evidence file not found: {evidence_path}{Colors.ENDC}")
            return
    else:
        evidence_path = prompt_for_evidence_file(platform_root)
        if not evidence_path:
            # User cancelled
            return

    try:
        skill_path = resolve_skill_path(platform_root)
    except FileNotFoundError as e:
        if not args.silent:
            print(f"{Colors.FAIL}[!] {e}{Colors.ENDC}")
        return

    # For transparency, echo what we are about to do
    if not args.silent:
        clear_screen()
        print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
        print(" BUILDING AI PROMPT FOR SPLUNK NOTABLE ".center(80))
        print("=" * 80 + f"{Colors.ENDC}")
        print(f"\n{Colors.GREEN}[+] Using skill:{Colors.ENDC} {os.path.basename(skill_path)}")
        print(f"{Colors.GREEN}[+] Evidence file:{Colors.ENDC} {evidence_path}")

    # Generate the fused prompt file
    try:
        generate_prompt(skill_path, evidence_path, output_dir, silent=args.silent)
    except Exception as e:
        if not args.silent:
            print(f"{Colors.FAIL}[!] Error generating AI prompt: {e}{Colors.ENDC}")
        return

    if not args.silent and not os.environ.get("COMMANDER_BOOT"):
        print(f"\n{Colors.CYAN}[i] Next step: Open the generated AI_Prompt_* file in Data/Active_Workspace "
              f"and paste it into your approved IL5 LLM to obtain SPL, disposition, and the two-sentence closing statement.{Colors.ENDC}")

        # Interactive paste-back of the IL5 AI response so we can
        # rehydrate it immediately using the sanitizer's mapping.
        print(f"\n{Colors.HEADER}--- PASTE IL5 AI RESPONSE (END on its own line to finish) ---{Colors.ENDC}")
        print("[*] After you paste the model's answer below, type 'END' on a new line.")
        print("[*] The helper will then substitute tokens (e.g., HOST_1, IPV4_1) back to real values locally.\n")

        response_lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip().upper() == 'END':
                break
            response_lines.append(line)

        if not response_lines:
            print(f"{Colors.WARNING}[!] No IL5 AI response pasted; skipping rehydration step.{Colors.ENDC}")
            input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")
            return

        response_text = "\n".join(response_lines)

        # Save the raw AI response to Active_Workspace
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        response_name = f"AI_Response_from_public_{timestamp}.txt"
        platform_root = get_platform_root()
        active_dir = os.path.join(platform_root, 'Data', 'Active_Workspace')
        os.makedirs(active_dir, exist_ok=True)
        response_path = os.path.join(active_dir, response_name)
        with open(response_path, 'w', encoding='utf-8') as f:
            f.write(response_text)

        # Load the latest sanitizer mapping (tokens → real values)
        map_path = os.path.join(active_dir, 'Sanitized_Text_latest.map.json')
        mapping = {}
        if os.path.exists(map_path):
            try:
                with open(map_path, 'r', encoding='utf-8') as mf:
                    import json
                    mapping = json.load(mf)
            except Exception as e:
                print(f"{Colors.WARNING}[!] Could not read mapping file {map_path}: {e}{Colors.ENDC}")
        else:
            print(f"{Colors.WARNING}[!] Mapping file not found: {map_path}. Tokens will not be rehydrated.{Colors.ENDC}")

        # Load optional Splunk env profile for role→SPL expansion
        env_profile = {}
        global_config_dir = os.path.join(platform_root, 'Tools', 'global_config')
        env_profile_path = os.path.join(global_config_dir, 'splunk_env_profile.json')
        if os.path.exists(env_profile_path):
            try:
                with open(env_profile_path, 'r', encoding='utf-8') as epf:
                    import json
                    env_profile = json.load(epf)
            except Exception as e:
                print(f"{Colors.WARNING}[!] Could not read Splunk env profile {env_profile_path}: {e}{Colors.ENDC}")

        # Apply token substitution from the sanitizer mapping and then
        # expand any role tokens using the env profile.
        rehydrated_text = response_text
        if mapping:
            for token, value in mapping.items():
                rehydrated_text = rehydrated_text.replace(token, value)

        if env_profile:
            for role, fragment in env_profile.items():
                rehydrated_text = rehydrated_text.replace(role, fragment)

        rehydrated_name = f"Rehydrated_{response_name}"
        rehydrated_path = os.path.join(active_dir, rehydrated_name)
        try:
            with open(rehydrated_path, 'w', encoding='utf-8') as rf:
                rf.write(rehydrated_text)
            print(f"\n{Colors.GREEN}[+] Rehydrated AI response saved to:{Colors.ENDC} {rehydrated_path}")
            try:
                if os.name == 'nt':
                    os.startfile(rehydrated_path)
            except Exception:
                pass
        except Exception as e:
            print(f"{Colors.WARNING}[!] Could not write rehydrated response: {e}{Colors.ENDC}")

        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")


if __name__ == '__main__':
    main()

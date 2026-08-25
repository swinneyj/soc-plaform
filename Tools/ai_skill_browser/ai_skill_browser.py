# TOOL_NAME: AI_Skill_Browser
# DESC: Interactive browser for SOC AI SKILL files stored in the Knowledge Base.
# CATEGORY: System Utilities

import os
import sys
import subprocess

# Dynamically add the Tools directory to the path so we can load core_lib
current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root


def load_skills(kb_dir):
    """Discover SKILL files in the Knowledge Base with a one-line preview."""
    skills = []
    if not os.path.exists(kb_dir):
        return skills

    for file in sorted(os.listdir(kb_dir)):
        if not file.endswith('.md'):
            continue
        if file == 'Skill_Index.md':
            continue

        path = os.path.join(kb_dir, file)
        base_name = file
        if base_name.endswith('_SKILL.md'):
            base_name = base_name[:-10]  # strip '_SKILL.md'
        elif base_name.endswith('.md'):
            base_name = base_name[:-3]

        preview = "No description available."
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    if line.startswith('#'):
                        # Skip pure headings; many SKILL files start with a title.
                        continue
                    preview = line[:100] + ('...' if len(line) > 100 else '')
                    break
        except Exception:
            pass

        skills.append({
            'file': file,
            'name': base_name,
            'path': path,
            'preview': preview,
        })

    return skills


def show_skill_detail(skill):
    """Print key information and a short content snippet for a single skill."""
    clear_screen()
    print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
    print(" AI SKILL DETAIL ".center(80))
    print("=" * 80 + f"{Colors.ENDC}")

    print(f"Name:        {skill['name']}")
    print(f"File:        {skill['file']}")
    print(f"Location:    {skill['path']}")
    print(f"Preview:     {skill['preview']}")

    print(f"\n{Colors.HEADER}--- CONTENT SNIPPET (first ~40 lines) ---{Colors.ENDC}")
    try:
        with open(skill['path'], 'r', encoding='utf-8', errors='ignore') as f:
            for i in range(40):
                line = f.readline()
                if not line:
                    break
                print(line.rstrip())
    except Exception as e:
        print(f"{Colors.FAIL}[!] Error reading skill file: {e}{Colors.ENDC}")

    input(f"\n{Colors.BLUE}Press Enter to return to Skill Browser...{Colors.ENDC}")


def find_skill(skills, identifier):
    """Resolve a user identifier (index or name) to a skill dict."""
    identifier = identifier.strip()
    if not identifier:
        return None

    if identifier.isdigit():
        idx = int(identifier) - 1
        if 0 <= idx < len(skills):
            return skills[idx]

    ident_lower = identifier.lower()
    for s in skills:
        if s['name'].lower() == ident_lower or s['file'].lower() == ident_lower:
            return s

    return None


def main():
    platform_root = get_platform_root()
    kb_dir = os.path.join(platform_root, 'Data', 'AI_Knowledge_Base')

    while True:
        skills = load_skills(kb_dir)
        clear_screen()

        print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
        print(" AI SKILL BROWSER ".center(80))
        print("=" * 80 + f"{Colors.ENDC}")
        print(f"Knowledge Base: {kb_dir}\n")

        if not skills:
            print(f"{Colors.WARNING}[!] No SKILL files found. Run AI_Skill_Harvester first or add SKILL.md files to the Knowledge Base.{Colors.ENDC}")
            cmd = input(f"\n{Colors.BLUE}skills>{Colors.ENDC} ").strip().lower()
            if cmd in ['0', 'back', 'exit', 'quit']:
                break
            continue

        print(f"{Colors.HEADER}--- AVAILABLE AI SKILLS ---{Colors.ENDC}")
        for i, skill in enumerate(skills, start=1):
            print(f" {Colors.GREEN}[{i:2d}]{Colors.ENDC} {skill['name']:<40} | {skill['preview']}")

        print(f"\n{Colors.CYAN}" + "-" * 80 + f"{Colors.ENDC}")
        print("Commands: [ID] to view | 'open [ID or name]' | 'search [keyword]' | 'refresh' | 'back'")

        raw_cmd = input(f"\n{Colors.BLUE}skills>{Colors.ENDC} ").strip()
        cmd_lower = raw_cmd.lower()

        if cmd_lower in ['0', 'back', 'exit', 'quit']:
            break

        if not raw_cmd:
            continue

        if cmd_lower == 'refresh':
            continue

        if cmd_lower.startswith('open '):
            ident = raw_cmd.split(' ', 1)[1]
            skill = find_skill(skills, ident)
            if skill:
                try:
                    print(f"\n{Colors.WARNING}[*] Opening {skill['file']} in Notepad...{Colors.ENDC}")
                    subprocess.Popen(['notepad.exe', skill['path']])
                except Exception as e:
                    print(f"{Colors.FAIL}[!] Failed to open skill file: {e}{Colors.ENDC}")
                input(f"\n{Colors.BLUE}Press Enter to return to Skill Browser...{Colors.ENDC}")
            else:
                print(f"{Colors.FAIL}[!] Skill not found for identifier: {ident}{Colors.ENDC}")
                input(f"\n{Colors.BLUE}Press Enter to return...{Colors.ENDC}")
            continue

        if cmd_lower.startswith('search '):
            keyword = cmd_lower.split(' ', 1)[1].strip()
            if not keyword:
                continue
            print(f"\n{Colors.CYAN}[*] Searching skills for: '{keyword}'...{Colors.ENDC}")
            found = False
            for i, skill in enumerate(skills, start=1):
                if keyword in skill['name'].lower() or keyword in skill['preview'].lower():
                    print(f" {Colors.GREEN}[{i:2d}]{Colors.ENDC} {skill['name']:<40} | {skill['preview']}")
                    found = True
            if not found:
                print(f"{Colors.WARNING}[-] No skills matched that keyword.{Colors.ENDC}")
            input(f"\n{Colors.BLUE}Press Enter to return...{Colors.ENDC}")
            continue

        # Default: treat as ID to view details
        skill = find_skill(skills, raw_cmd)
        if skill:
            show_skill_detail(skill)
        else:
            print(f"{Colors.FAIL}[!] Invalid command or skill identifier.{Colors.ENDC}")
            input(f"\n{Colors.BLUE}Press Enter to return...{Colors.ENDC}")

    if not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")


if __name__ == "__main__":
    main()

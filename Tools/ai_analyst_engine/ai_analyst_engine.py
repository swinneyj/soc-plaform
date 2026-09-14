# TOOL_NAME: AI_Analyst_Engine
# DESC: Fuses an AI Skill from the Knowledge Base with raw evidence to generate a structured analysis prompt.
# CATEGORY: Reporting
# ARG: --evidence | Evidence File | Path to the file containing raw logs or data to analyze | True

import os
import sys
import argparse
import datetime
import shutil

# Dynamically add the Tools directory to the path so we can load core_lib
current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root

def list_available_skills(kb_dir):
    """Returns a list of all SKILL.md files in the Knowledge Base."""
    skills = []
    if not os.path.exists(kb_dir):
        return skills
        
    for file in os.listdir(kb_dir):
        if file.endswith('.md') and file != 'Skill_Index.md':
            skills.append(file)
    return sorted(skills)

def generate_prompt(skill_path, evidence_path, output_dir, detection_science_path=None, silent=False):
    """Combines the skill, detection science logic, and evidence into a single prompt file."""
    try:
        with open(skill_path, 'r', encoding='utf-8', errors='ignore') as f:
            skill_content = f.read()
            
        with open(evidence_path, 'r', encoding='utf-8', errors='ignore') as f:
            evidence_content = f.read()

        detection_science_content = ""
        if detection_science_path and os.path.exists(detection_science_path):
            with open(detection_science_path, 'r', encoding='utf-8', errors='ignore') as f:
                detection_science_content = f.read()
            
    except Exception as e:
        if not silent: print(f"{Colors.FAIL}[!] Error reading files: {e}{Colors.ENDC}")
        return

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    skill_name = os.path.basename(skill_path).replace('_SKILL.md', '')
    evidence_name = os.path.basename(evidence_path)
    
    sections = [
        "You are acting as an expert SOC Analyst. Please apply the specialized methodology, detection science, and evidence below.",
        f"\n==================================================\n[ANALYST SKILL]: {skill_name}\n==================================================\n{skill_content}"
    ]

    if detection_science_content:
        sections.append(
            f"\n==================================================\n[DETECTION SCIENCE & RULE LOGIC]\n==================================================\n{detection_science_content}"
        )

    sections.append(
        f"\n==================================================\n[RAW EVIDENCE]: {evidence_name}\n==================================================\n{evidence_content}"
    )
    sections.append(
        "\n==================================================\nPlease generate your analysis now based strictly on the methodology, detection science, and evidence above."
    )

    prompt = "\n".join(sections)
    out_file = os.path.join(output_dir, f"AI_Prompt_{skill_name}_{timestamp}.txt")
    
    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(prompt)

    latest_path = os.path.join(output_dir, 'AI_Prompt_latest.txt')
    try:
        shutil.copy2(out_file, latest_path)
    except Exception:
        pass

    if not silent:
        print(f"\n{Colors.GREEN}[+] AI Analysis Prompt with Detection Science successfully generated!{Colors.ENDC}")
        print(f"[*] Saved to: {out_file}")
        print(f"\n{Colors.CYAN}[i] Next Step: Copy the contents of this text file and paste it into your approved IL5 LLM.{Colors.ENDC}")

        # Convenience: automatically open the prompt in the default
        # text editor (Notepad on Windows) so the analyst can review
        # or copy it immediately after generation.
        try:
            if os.name == 'nt':
                os.startfile(out_file)
        except Exception:
            # Failure to auto-open should not break prompt generation.
            pass

def main():
    parser = argparse.ArgumentParser(description="AI Analyst Engine")
    parser.add_argument('--evidence', type=str, required=True, help="Path to the raw evidence file")
    parser.add_argument('--silent', action='store_true', help="Suppress output")
    args = parser.parse_args()

    platform_root = get_platform_root()
    kb_dir = os.path.join(platform_root, 'Data', 'AI_Knowledge_Base')
    output_dir = os.path.join(platform_root, 'Data', 'Active_Workspace')
    os.makedirs(output_dir, exist_ok=True)

    evidence_path = args.evidence.strip('"').strip("'")
    if not os.path.isabs(evidence_path) and not os.path.exists(evidence_path):
        # Allow relative paths from the platform root for playbook automation
        candidate = os.path.join(platform_root, evidence_path)
        if os.path.exists(candidate):
            evidence_path = candidate
        
    if not os.path.exists(evidence_path):
        if not args.silent: print(f"{Colors.FAIL}[!] Evidence file not found: {evidence_path}{Colors.ENDC}")
        return

    skills = list_available_skills(kb_dir)
    if not skills:
        if not args.silent: print(f"{Colors.FAIL}[!] No skills found in {kb_dir}. Run the Harvester first.{Colors.ENDC}")
        return

    if not args.silent:
        clear_screen()
        print(f"{Colors.CYAN}{Colors.BOLD}" + "="*80)
        print(" AI ANALYST ENGINE ".center(80))
        print("="*80 + f"{Colors.ENDC}")
        print(f"[*] Target Evidence: {os.path.basename(evidence_path)}\n")
        
        print(f"{Colors.HEADER}--- SELECT ANALYSIS METHODOLOGY ---{Colors.ENDC}")
        for i, skill in enumerate(skills):
            print(f" {Colors.GREEN}[{i+1:2d}]{Colors.ENDC} {skill}")
            
        print(f" {Colors.GREEN}[ 0]{Colors.ENDC} Cancel and Exit")
        
        choice = input(f"\n{Colors.BLUE}Select Skill ID to apply to evidence:{Colors.ENDC} ").strip()
        
        if choice == '0' or choice.lower() in ['exit', 'quit']:
            return
            
        try:
            skill_idx = int(choice) - 1
            if 0 <= skill_idx < len(skills):
                selected_skill = skills[skill_idx]
                skill_path = os.path.join(kb_dir, selected_skill)
                # Use keyword arguments so the detection_science_path parameter
                # remains optional and the silent flag is correctly applied.
                generate_prompt(skill_path, evidence_path, output_dir, silent=args.silent)
            else:
                print(f"{Colors.FAIL}[!] Invalid selection.{Colors.ENDC}")
        except ValueError:
            print(f"{Colors.FAIL}[!] Invalid input. Please enter a number.{Colors.ENDC}")

    if not args.silent and not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")

if __name__ == "__main__":
    main()

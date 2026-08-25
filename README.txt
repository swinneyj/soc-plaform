==================================================
        SOC ORCHESTRATION PLATFORM
==================================================

QUICK START:
1. Ensure Python 3 is installed on your system.
2. Double-click 'launch_commander.bat' to open the central menu.
3. Type the ID number or Name of a tool to run it.

COMMANDS:
* reload       - Scans the folder for new tools and updates the menu.
* info [Name]  - View details about a specific tool.
* edit [Name]  - Opens the tool's source code in Notepad.
* exit         - Closes the orchestrator safely.

==================================================
        INSTALLED TOOLS DIRECTORY
==================================================

--- CORE ORCHESTRATION ---
* Playbook_Runner
  -> Executes pre-defined JSON sequences of SOC tools (Playbooks) for rapid automated workflows.
* SOC_Commander
  -> The central orchestrator for the SOC platform. Manages tool execution, registry indexing, and user interface.

--- DATA SANITIZATION ---
* Data_Ingestor_Pipeline
  -> Unified pipeline for ingesting files (or folders), sanitizing PII/PHI using pseudo-NLP, scrubbing network IOCs, and formatting data.

--- INTEL COLLECTION ---
* IOC_Extractor
  -> High-fidelity extractor that pulls IPs, MACs, URLs, and Hashes from files. Defangs outputs for safe playbook automation.
* Threat_Intel_Fetcher
  -> Aggregates live intel from CISA KEV, CISA Advisories, and Abuse.ch into a single comprehensive daily bundle. Ready for playbook automation.
* base64_decoder
  -> Extracts, cleans, and decodes Base64 payloads from raw text or files. Ideal for de-obfuscating PowerShell payloads or malicious scripts.
* cve_intel_fetcher
  -> Instantly queries the NIST NVD database for a specific CVE to return its CVSS score, summary, and exploit status.
* pki_cert_decoder
  -> Decodes and analyzes X.509 certificates (.pem, .cer, .der, or raw base64) locally without external network calls.

--- REPORTING ---
* AI_Analyst_Engine
  -> Fuses an AI Skill from the Knowledge Base with raw evidence to generate a structured analysis prompt.
* AI_Response_Rehydrator
  -> Replaces placeholder tokens (e.g., HOST_1, IPV4_1) in an AI response with real values from the sanitizer mapping file.
* Automated_Reporter
  -> Compiles text summaries and sanitized intelligence into a standardized, shareable HTML SOC briefing document.
* Case_Bundle_Builder
  -> Builds a consolidated incident case bundle from a folder of exports.
* Splunk_Notable_AI_Helper
  -> Guided helper that takes a sanitized Splunk ES Incident Review notable export and builds an AI prompt asking for SPL, disposition, and a two-sentence closing comment.
* mde_triage_aggregator
  -> Designed EXCLUSIVELY for raw Microsoft Defender (MDE) CSV exports. Use this to deduplicate incident storms, prune false positive noise, and format High-Severity alerts for shift handoff.
* splunk_notable_parser
  -> Parses raw Splunk exports using aggressive Regex to extract actionable IOCs (IPs, Emails, Hashes) hidden within unstructured log data.

--- SYSTEM UTILITIES ---
* AI_Context_Exporter
  -> Bundles source code, configs, and directory structure into a single text file for AI context sharing.
* AI_Skill_Browser
  -> Interactive browser for SOC AI SKILL files stored in the Knowledge Base.
* AI_Skill_Harvester
  -> Scans the user Downloads directory for scattered AI SKILL.md files, centralizes them, and generates an index.
* Dir_Mapper
  -> Generates a comprehensive, indented file and folder map of the Downloads directory and saves it to the root platform folder.
* Maintenance_Suite
  -> Interactive hub for cleaning the workspace, generating AI context exports, or backing up the platform.
* Platform_Blueprint
  -> Scans the SOC platform to map all folder paths, files, metadata, and source code into a single document.
* Platform_Health_Check
  -> Validates Commander registry, tool scripts, and playbook references for basic integrity.
* System_Downloads_Mapper
  -> Scans your actual Windows User Downloads folder and generates a complete file map saved to the SOC root.
* Text_Sanitizer_Pipeline
  -> Reflows garbled text and sanitizes IPs, MACs, and common PII/PHI before opening in Notepad.
* Tool_Packager
  -> Compresses the SOC platform into a ZIP, injecting a resilient launcher and dynamically generated README.
* Workspace_Cleaner
  -> Organizes legacy tools into archives, consolidates paperwork, and safely removes empty directories.
* text_reformatter
  -> Reflows garbled text with hard-wrapped lines into cleaner paragraphs for AI analysis.

--- UNCATEGORIZED ---
* filecheck
  -> Reliably counts lines, searches for phrases, and hashes a file (Interactive).
* smart_router.py
  -> No description provided.
* tool_indexer.py
  -> No description provided.
* utils.py
  -> No description provided.

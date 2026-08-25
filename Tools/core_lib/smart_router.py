import os
import csv
import re
from core_lib.utils import Colors

def detect_file_type(filepath, silent=True):
    """Analyzes a file to determine its context and recommend tools."""
    if not os.path.exists(filepath):
        return []
        
    filename = os.path.basename(filepath).lower()
    ext = os.path.splitext(filename)[1]
    
    recommended_tools = set()

    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            sample_content = f.read(2048)
            
            # --- 1. MDE CSV DETECTION ---
            if ext == '.csv':
                f.seek(0)
                try:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        headers = [h.lower() for h in reader.fieldnames]
                        if 'incident name' in headers or 'alert name' in headers:
                            recommended_tools.add('mde_triage_aggregator')
                            recommended_tools.add('ioc_extractor')
                except Exception: pass
            
            # --- 2. PKI CERTIFICATE DETECTION ---
            if ext in ['.pem', '.cer', '.der', '.crt'] or '-----BEGIN CERTIFICATE-----' in sample_content:
                recommended_tools.add('pki_cert_decoder')
                
            # --- 3. BASE64 PAYLOAD DETECTION ---
            if re.search(r'(?:[A-Za-z0-9+/]{60,})(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?', sample_content):
                recommended_tools.add('base64_decoder')
                recommended_tools.add('ioc_extractor')
                
            # --- 4. CVE IDENTIFIER DETECTION ---
            if re.search(r'\bCVE-\d{4}-\d{4,}\b', sample_content, re.IGNORECASE):
                recommended_tools.add('cve_intel_fetcher')
                
            # --- 5. RAW TEXT/LOG DETECTION ---
            if ext in ['.txt', '.log', '.md'] or ('{' not in sample_content and '<' not in sample_content):
                if re.search(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', sample_content) or '@' in sample_content:
                    recommended_tools.add('ioc_extractor')
                    recommended_tools.add('data_ingestor_pipeline')

                # Always offer the text sanitizer for free-form text destined for AI
                recommended_tools.add('Text_Sanitizer_Pipeline')

                # --- 6. GENERIC FALLBACK ---
                # Always recommend basic analysis tools for plain text files
                recommended_tools.add('filecheck')
                recommended_tools.add('AI_Analyst_Engine')
                    
    except Exception as e:
        if not silent: print(f"{Colors.FAIL}[!] Error analyzing file: {e}{Colors.ENDC}")

    return list(recommended_tools)

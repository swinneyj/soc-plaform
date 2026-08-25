import sqlite3
conn = sqlite3.connect('/app/splunk-es-backup-toolkit/triage.db')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(triage_results)")
columns = [row[1] for row in cursor.fetchall()]
if 'rule_id' not in columns:
    print('Adding rule_id column...')
    cursor.execute('ALTER TABLE triage_results ADD COLUMN rule_id TEXT;')
    conn.commit()
    print('Column added')
conn.close()
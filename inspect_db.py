import sqlite3
import json

db_path = 'splunk-es-backup-toolkit/triage.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()

print(f"Found {len(tables)} tables:\n")

schema = {}
for table in tables:
    table_name = table[0]
    print(f"\n{table_name}:")
    
    # Get table schema
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    
    col_info = []
    for col in columns:
        col_id, col_name, col_type, not_null, default, pk = col
        col_info.append(f"  {col_name} ({col_type})")
        print(f"  {col_name} ({col_type})")
    
    schema[table_name] = col_info
    
    # Get row count
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"  Rows: {count}")

conn.close()

print("\n\n=== SCHEMA JSON ===")
print(json.dumps(schema, indent=2))

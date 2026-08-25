# Daily Workflow

## Shared Logic Changes

Use this when you are changing shared rules, supportive SPL queries, or baked logic.

### Maintainer who changes shared logic

1. Edit repo-managed files such as:
   - `sample_rules.json`
   - `supportive_rules.json`
   - tool scripts or templates
2. Validate or review the change.
3. Commit and push:

```powershell
git add .
git commit -m "Update shared rule logic"
git push
```

### Other user after pulling Git changes

1. Pull the repo updates:

```powershell
git pull
```

2. Re-apply shared logic into the local PostgreSQL database:

```powershell
.\scripts\sync_shared_logic_to_db.ps1
```

## Local Alert and Case Data

Use this only when local alert or case data needs to be handed off.

### Export latest local DB to the share

```powershell
.\scripts\export_db_dump_to_share.ps1
```

### Restore latest shared DB dump from the share

```powershell
.\scripts\restore_db_dump_from_share.ps1
```

## Drift Check

If you want to see whether the local DB shared-logic tables still match the repo JSON files:

```powershell
python .\scripts\check_shared_logic_drift.py
```

## Export DB-Backed Shared Logic For Review

If you need to inspect the DB's current rule/query state and compare it manually before updating repo JSON:

```powershell
python .\scripts\export_shared_logic_from_db.py
```
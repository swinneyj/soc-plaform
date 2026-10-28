# Fake Splunk Demo — a full investigation loop, no credentials

*Created Oct 23, 2026. Companion to `docs/SPLUNK_REHEARSAL.md`: that document is
the runbook for the **real** instance; this one runs the same platform loop
against `scripts/fake_splunk.py` with a narrative corpus.*

**Scope guard:** the corpus proves the *platform's* loop — notable → case →
cards → evidence → disposition. It is not Splunk's contract: the SPL subset is
the mock backend's, the certificate is optional and self-signed, and nothing
here substitutes for the live rehearsal's exit criteria.

## The corpus

`scripts/seed_demo_corpus.py` generates **500 events** across seven indexes,
anchored to the moment you run it:

| Index | Events | Content |
|---|---|---|
| `wineventlog` | 155 | 4624 logons, 4688 process starts |
| `edr` | 117 | process lineage, file writes, registry, LSASS access |
| `proxy` | 123 | C2 beacon GETs, the exfil POST, benign browsing |
| `netflow` | 55 | outbound volume, internal SMB |
| `auth` | 45 | VPN success/failure history (Ashburn/Reston/Bucharest) |
| `itsm` | 4 | change records — **never** for the case host |
| `notable` | 1 | the ES notable that starts the case |

**The story:** `FINWS07` opens a macro document (`mrivera`), Word spawns
hidden encoded PowerShell, a payload and Run key land, LSASS is touched, the
host beacons to `cdn-sync.example.net` every two minutes, moves laterally to
`FS-01`/`DC-01`, then the same user fails VPN nine times from Ashburn and
succeeds from **Bucharest** on an unmanaged device — followed by a 42 MB POST
and a 2.1 GB outbound flow. Baseline noise on ten other hosts means
`$host$`/`$user$` filters have to actually filter, and the "approved change"
card legitimately returns nothing.

## Run it

```bash
.venv314/bin/python scripts/seed_demo_corpus.py            # regenerate, anchored to now
.venv314/bin/python scripts/fake_splunk.py \
  --seed services/mock_splunk/seed/demo_events.jsonl       # port 18089 by default

# in the shell that launches the platform (dummy token, never a real one):
SEARCH_BACKEND=splunk SPLUNK_URL=http://127.0.0.1:18089 SPLUNK_TOKEN=dev-token \
  bash scripts/start
```

Then, in the UI:

1. **Paste** `services/mock_splunk/seed/demo_notable.txt` into the notable box
   (leave *Redact* at its default).
2. **Promote** it to a case — the verdict stays `suspicious`; the intake never
   pre-judges.
3. **Run** the cards. Expected, against this corpus:

| Card | Expected |
|---|---|
| Encoded PowerShell lineage on case host | 1 row — `parent_process=WINWORD.EXE count=2` |
| Outbound destinations for case host | 7 rows — `cdn-sync.example.net count=25` on top |
| VPN logon history for case user | 3 rows — Ashburn, Reston, Bucharest |
| Lateral logons from case host | 2 rows — `DC-01`, `FS-01` |
| Malware hashes seen on case host | 1 row — the `invoice.dll` hash |
| Approved change records for case host | **0 rows** → `no_results` (that is the finding) |

The generator also writes `demo_manifest.json` (anchor, counts, window,
regenerate command) and prints a **smoke table** of every card's rendered SPL
and row count before it exits non-zero, so a corpus that cannot answer its own
cards fails at generation time.

## Gotchas this corpus is built around

- **Staleness.** A corpus anchored weeks ago answers nothing inside `-7d`/`-2h`,
  which looks like a clean investigation instead of a broken fixture. The fake
  warns on startup when the newest event is more than 48 h old; regenerate
  before demoing.
- **Redaction.** The paste pipeline tokenizes every `word-word` token
  (`FIN-WS-07` → `HOST_6`) when redaction is on — the UI default. Since
  `$host$`/`$user$` are substituted *after* that, a hyphenated hostname would
  make every card query a token. The demo therefore uses `FINWS07`,
  `DEMO_RULE_PS_EXFIL`, and a hyphen-free rule name; a test pins that the
  notable survives the default redaction unchanged.
- **SPL subset.** Cards are grounded onto stored `SupportiveQuery` rows, so the
  surface is bounded by what you seed: base terms with `*`/`?` wildcards,
  `| search`, `| where`, `| stats count by <field>`, `| head N`. Anything else
  fails loudly with `unsupported SPL operator` — better than silently empty.
- **The model is still the model.** Cards come from the LLM and the disposition
  is derived from its verdict text; this corpus makes the *evidence* real, not
  the judgment. Run Ollama (or a scripted analyst backend) before expecting a
  disposition.

## Optional: seed the playbooks (writes to the configured DB)

```bash
.venv314/bin/python scripts/seed_demo_corpus.py --db
```

Upserts the demo rule (`DEMO_RULE_PS_EXFIL`) and its six cards, idempotently.
The generator prints the database host (never credentials). If `DATABASE_URL`
points at the shared Neon database, that is a **production write**: it adds
only the rule and its queries — the case itself still comes from paste →
promote — and rows carry the `DEMO_` prefix so they are easy to find and
remove. Follow the `AGENTS.md` "one shared production dataset" protocol before
running it there; for a sandbox, point `DATABASE_URL` at a local Postgres.

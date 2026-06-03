# ares-dph-isir

Skill pro Claude Code — ověření české firmy ze tří veřejných státních registrů:

| Registr | Co vrací |
|---|---|
| **ARES** | Název, sídlo, právní forma, CZ-NACE, datum vzniku, jednatele |
| **DPH** | DIČ, stav registrace plátce DPH |
| **ISIR** | Insolvence ano/ne, počet záznamů |

Všechna API jsou veřejná — žádný API klíč není potřeba.

## Instalace

```bash
git clone git@github.com:semeradoval/ares-dph-isir.git ~/.claude/skills/ares-dph-isir
pip install -r ~/.claude/skills/ares-dph-isir/scripts/requirements.txt
```

Nebo přes install skript:

```bash
curl -fsSL https://raw.githubusercontent.com/semeradoval/ares-dph-isir/main/install.sh | bash
```

## Použití

V Claude Code stačí napsat:

```
/ares-dph-isir 12345678
/ares-dph-isir Ukázková firma s.r.o.
/ares-dph-isir Testovací servis Novák
```

## Požadavky

- Python 3.8+
- `pyyaml` (`pip install pyyaml`)

## Skripty samostatně

```bash
python3 scripts/ares_lookup.py --ico 12345678
python3 scripts/ares_lookup.py --nazev "Ukázková firma" --limit 5
python3 scripts/dph_check.py --ico 12345678
python3 scripts/insolvence_check.py --ico 12345678
```

## Licence

MIT

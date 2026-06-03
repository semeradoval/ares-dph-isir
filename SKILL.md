---
name: ares-dph-isir
description: Ověření firmy z českých státních registrů — ARES (profil, sídlo, CZ-NACE, jednatele), registr plátců DPH a insolvenční rejstřík ISIR. Použij když potřebuješ zjistit základní informace o firmě podle IČO nebo názvu.
---

# Skill: ares-dph-isir

Ověří firmu ze tří veřejných registrů najednou:

- **ARES** — název, sídlo, právní forma, CZ-NACE, datum vzniku, jednatele (pokud jsou v ROS)
- **DPH** — DIČ, stav registrace plátce DPH
- **ISIR** — insolvence ano/ne, počet záznamů v insolvenčním rejstříku

Všechna tři API jsou veřejná, bez nutnosti API klíče.

## Spuštění

Uživatel zadá IČO nebo název firmy:

```
/ares-dph-isir 24165905
/ares-dph-isir NEWIS TRADE
/ares-dph-isir Boxaro Žamberk
```

## Workflow

### 1. Zjisti vstup

Z uživatelovy zprávy extrahuj:
- **IČO** — pokud jde o 8místné číslo (doplň nuly zleva na 8 číslic)
- **Název firmy** — jinak

### 2. Dohledej IČO (pokud chybí)

```bash
python3 scripts/ares_lookup.py --nazev "<název>" [--mesto "<město>"] --limit 10
```

- Pokud vrátí **právě 1 kandidáta** → použij ho automaticky
- Pokud vrátí **více kandidátů** → vypiš je uživateli a zeptej se na výběr
- Pokud vrátí **0 kandidátů** → oznám uživateli a skonči

### 3. Spusť paralelně všechny tři ověřovací skripty

```bash
python3 scripts/ares_lookup.py --ico <ICO>
python3 scripts/dph_check.py --ico <ICO>
python3 scripts/insolvence_check.py --ico <ICO>
```

### 4. Zobraz výsledek

Výstup prezentuj přehledně v češtině. Povinné sekce:

```
Firma: <název> (<právní forma>)
IČO: <ico> | DIČ: <dic nebo "neplátce DPH">
Sídlo: <adresa>
Vzniklá: <datum> | Stav: <aktivní/likvidace/...>
CZ-NACE: <kódy a popisy>
Jednatele: <jméno(a) nebo "nedostupné z ARES">
DPH: <plátce / neplátce> [stav registrace]
Insolvence: <čistá / ⚠️ POZOR — N záznamů v ISIR>
```

Pokud některý zdroj selže (síťová chyba, timeout), pokračuj s ostatními a
v příslušné sekci uveď `chyba: <popis>`.

## Poznámky

- ARES VR (jednatele) vrací prázdné pole u nových s.r.o. — normální stav,
  pokud je firma mladší než ~3 měsíce
- ISIR může být dočasně nedostupný (HTTP 500) — v takovém případě informuj
  uživatele a navrhni opakování
- DPH DIČ u OSVČ bývá ve formátu rodného čísla (CZ + 9–10 číslic) — je to
  správně, ne chyba

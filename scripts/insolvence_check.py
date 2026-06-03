#!/usr/bin/env python3
"""
insolvence_check.py — kontrola insolvenčního rejstříku ISIR.

Volá veřejné webové rozhraní ISIR, formulář "lustrace":
  https://isir.justice.cz/isir/ueu/vysledek_lustrace.do?ic=...&aktualnost=AKTUALNI_I_UKONCENA

Parsuje HTML odpověď a extrahuje počet dlužníků + per záznam: spisová značka,
soud, jméno, IČ, sídlo, stav řízení.

## Obrana proti tichému selhání parseru

Parser je založený na regex na cleaned-up textu HTML — jakákoli změna ISIR
HTML může vést k tomu, že některé pole přestane extrahovat. Aby to nebylo
tiché, skript implementuje **čtyři vrstvy obrany**:

  1) **Verzový kanárek**: ISIR v patičce uvádí verzi aplikace
     ('Insolvenční rejstřík (1.26.0.0)'). Skript ji extrahuje a porovná
     s konstantou ISIR_TESTED_VERSION. Změna = warning.

  2) **Strukturní validace**:
     - pocet_dluzniku == len(zaznamy)
     - každý záznam má všechna povinná pole z REQUIRED_FIELDS
     - žádné pole není podezřele dlouhé / podezřele prázdné

  3) **Health pole** ve výstupu:
     - parser_health: "ok" | "warn" | "chyba"
     - parser_warnings: list konkrétních problémů
     - isir_app_version: zjištěná verze
     Volající (skill workflow) má kontrolovat parser_health a hlasitě
     reportovat warnings/chyby do souhrnu běhu.

  4) **Forenzní dump**: při warning/chyba se cleaned-up text + raw HTML
     uloží do data/raw/isir_html_dumps/{ico}_{ts}.{txt,html} a cesty se
     vrátí v poli debug_dumps. Tyto dumps jsou gitignored.

Skript **nikdy** neselhává s non-zero exit kódem — vrací JSON, který
volajícímu řekne stav.

Vstup:
  --ico <IČO>
  [--jen-aktualni]    pouze aktuální (běžící) řízení místo všech historických
  [--no-dump]         neukládat html/text dump ani při warning (jen pro testy)

Výstup (stdout, JSON):
  {
    "ico": "...",
    "isir_url": "...",
    "isir_app_version": "1.26.0.0",
    "pocet_dluzniku": 0,
    "insolvence": false,
    "zaznamy": [...],
    "platnost_k_datu": "10.04.2026 - 17.23",
    "parser_health": "ok",
    "parser_warnings": [],
    "debug_dumps": null,
    "chyba": null
  }
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ISIR_URL = "https://isir.justice.cz/isir/ueu/vysledek_lustrace.do"
USER_AGENT = (
    "sales-data-collection/1.0 "
    "(+https://github.com/dominikslechta1/sales_agent)"
)

# Verze ISIR aplikace, na které byl parser otestován. Když ISIR vrátí jinou
# verzi, parser sice může pořád fungovat, ale je to silný signál "tady se
# něco hnulo, podívej se".
ISIR_TESTED_VERSION = "1.26.0.0"

# Adresář pro forenzní dumpy. Cesta relativní k repo rootu (parent skriptu).
# Skript je v .claude/skills/sales-data-collection/scripts/, repo root je o 4 výš.
DUMP_DIR_REL = Path("data") / "raw" / "isir_html_dumps"

# Pole na úrovni "case" (insolvenční řízení) — jsou v záznamu jednou.
CASE_FIELD_PATTERNS: list[tuple[str, str]] = [
    ("spisova_znacka", r"Spisová značka:\s*(.*?)\s+Vedená"),
    ("soud", r"Vedená u\s+(.*?)\s+Jméno/název:"),
    ("stav_rizeni", r"Stav řízení:\s*(.*?)(?:\s+Zpět|\s+Detail|\s*$)"),
]

# Pole na úrovni "osoba" — jeden záznam (společný insolvenční návrh manželů)
# může obsahovat víc osob. ISIR používá pro PO label "Sídlo společnosti", pro
# FO label "Bydliště" — sjednocujeme je pod klíč "adresa".
PERSON_FIELD_PATTERNS: list[tuple[str, str]] = [
    ("jmeno", r"Jméno/název:\s*(.*?)\s+IČ:"),
    ("ic", r"IČ:\s*(\d*)"),  # u FO může chybět
    (
        "rodne_cislo_nar",
        r"Rodné číslo / Datum nar\.:\s*(.*?)\s+(?:Sídlo společnosti|Bydliště):",
    ),
    (
        "adresa",
        r"(?:Sídlo společnosti|Bydliště):\s*(.*?)(?=\s+Jméno/název:|\s+Stav řízení|\s*$)",
    ),
]

# Která case-level pole MUSÍ být v každém záznamu.
REQUIRED_CASE_FIELDS = {"spisova_znacka", "soud", "stav_rizeni"}
# Která person-level pole MUSÍ být v každé osobě.
REQUIRED_PERSON_FIELDS = {"jmeno"}

# Sanity limity — pole mimo tyto rozmezí jsou podezřelá.
MAX_FIELD_LEN = 500

RECORD_SPLIT_RE = re.compile(
    r"\d+\s*/\s*\d+\s+Detail\s+Obchodní rejstřík\s+(?=Spisová značka:)"
)


def normalize_ico(ico: str) -> str:
    ico = ico.strip().replace(" ", "")
    if not ico.isdigit():
        raise ValueError(f"IČO musí být číselné: {ico!r}")
    return ico.zfill(8)


def fetch_isir(ico: str, jen_aktualni: bool, timeout: float = 25.0) -> tuple[str, str]:
    aktualnost = "AKTUALNI" if jen_aktualni else "AKTUALNI_I_UKONCENA"
    params = {
        "ic": ico,
        "aktualnost": aktualnost,
        "rowsAtOnce": "50",
        "spis_znacky_obdobi": "VSE",
    }
    url = f"{ISIR_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return url, html


def html_to_text(html: str) -> str:
    """Vystrip HTML na čistý text se zachovanými mezerami."""
    s = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", "", s, flags=re.S | re.I)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_fields(blob: str, patterns: list[tuple[str, str]]) -> tuple[dict, list[str]]:
    """Vytáhne pole podle patternů. Vrací (extracted, podezřele_warnings)."""
    out: dict = {}
    podezrele: list[str] = []
    for field, pattern in patterns:
        m = re.search(pattern, blob, flags=re.S)
        if not m:
            continue
        value = m.group(1).strip(" .,;:")
        if not value:
            continue
        if len(value) > MAX_FIELD_LEN:
            podezrele.append(f"{field}=příliš dlouhé ({len(value)} znaků)")
            out[field] = value[:MAX_FIELD_LEN] + "..."
        else:
            out[field] = value
    return out, podezrele


def _parse_persons_in_record(blob: str, warnings: list[str], rec_idx: int) -> list[dict]:
    """V jednom záznamu (case) může být víc osob — typicky manželská insolvence
    se společným návrhem. Rozdělí blob podle 'Jméno/název:' (lookahead, abychom
    nezahodili separator) a z každého person-blobu vytáhne PERSON_FIELD_PATTERNS.
    """
    # Split na úseky začínající "Jméno/název:" — první úsek je case-header (zahodit).
    parts = re.split(r"(?=Jméno/název:)", blob)
    person_blobs = [p for p in parts if p.lstrip().startswith("Jméno/název:")]

    osoby: list[dict] = []
    for pi, pb in enumerate(person_blobs):
        # Odstřihni text po "Stav řízení:" (case-level pole) nebo po dalším Jméno
        cuts = []
        for marker in ("Stav řízení:",):
            i = pb.find(marker)
            if i > 0:
                cuts.append(i)
        if cuts:
            pb = pb[: min(cuts)]

        osoba, podezrele = _extract_fields(pb, PERSON_FIELD_PATTERNS)
        if osoba:
            osoby.append(osoba)
            missing = REQUIRED_PERSON_FIELDS - set(osoba.keys())
            if missing:
                warnings.append(
                    f"Záznam #{rec_idx + 1} osoba #{pi + 1}: chybí povinná pole {sorted(missing)}"
                )
            if podezrele:
                warnings.append(
                    f"Záznam #{rec_idx + 1} osoba #{pi + 1}: podezřelé hodnoty {podezrele}"
                )
    return osoby


def parse_records(text: str, warnings: list[str]) -> list[dict]:
    """Najde všechny záznamy dlužníků v textu lustrace.

    Strategie: rozdělit text na bloky podle prefixu "{N} / {Total} Detail
    Obchodní rejstřík". Z každého bloku vytáhnout case-level pole +
    `osoby` (typicky 1, ale u společných manželských návrhů 2+).

    Zachovává top-level pole pro zpětnou kompatibilitu (jmeno, ic, adresa,
    rodne_cislo_nar) — kopie z první osoby.
    """
    parts = RECORD_SPLIT_RE.split(text)
    record_blobs = parts[1:]

    records: list[dict] = []
    for idx, blob in enumerate(record_blobs):
        case_fields, case_podezrele = _extract_fields(blob, CASE_FIELD_PATTERNS)

        missing_case = REQUIRED_CASE_FIELDS - set(case_fields.keys())
        if missing_case:
            warnings.append(
                f"Záznam #{idx + 1}: chybí case-level pole {sorted(missing_case)}"
            )
        if case_podezrele:
            warnings.append(f"Záznam #{idx + 1}: podezřelé hodnoty {case_podezrele}")

        osoby = _parse_persons_in_record(blob, warnings, idx)

        if not osoby:
            warnings.append(
                f"Záznam #{idx + 1}: nepodařilo se najít žádnou osobu"
            )

        rec: dict = {
            **case_fields,
            "osoby": osoby,
            "pocet_osob": len(osoby),
        }

        # Zpětně kompatibilní top-level pole z první osoby
        if osoby:
            first = osoby[0]
            rec["jmeno"] = first.get("jmeno")
            rec["ic"] = first.get("ic") or None
            rec["rodne_cislo_nar"] = first.get("rodne_cislo_nar")
            rec["adresa"] = first.get("adresa")

        records.append(rec)

    return records


def extract_isir_version(text: str) -> str | None:
    """ISIR má v patičce string typu 'Insolvenční rejstřík (1.26.0.0)'."""
    m = re.search(r"Insolvenční rejstřík\s*\(\s*([\d.]+)\s*\)", text)
    return m.group(1) if m else None


def parse_response(html: str) -> dict:
    """Zparsuje HTML lustrace a vrátí strukturovaný dict + parser_health.

    Nikdy nehází výjimku — všechny problémy zaznamená do parser_warnings.
    """
    warnings: list[str] = []
    text = html_to_text(html)

    # 1) Verzový kanárek
    isir_version = extract_isir_version(text)
    if isir_version is None:
        warnings.append(
            "Verzový string ISIR nenalezen — patička HTML se možná změnila."
        )
    elif isir_version != ISIR_TESTED_VERSION:
        warnings.append(
            f"ISIR verze {isir_version} != testovaná {ISIR_TESTED_VERSION} "
            f"— parser sice může fungovat, ale prověř výstup ručně."
        )

    # 2) Hlavička: počet dlužníků + datum platnosti
    pocet_match = re.search(r"POČET NALEZENÝCH DLUŽNÍKŮ\s+(\d+)", text)
    pocet = int(pocet_match.group(1)) if pocet_match else None

    platnost_match = re.search(r"Údaje platné ke dni:\s*([^\s]+(?:\s+-\s+[^\s]+)?)", text)
    platnost = platnost_match.group(1).strip() if platnost_match else None

    if pocet is None:
        return {
            "pocet_dluzniku": None,
            "insolvence": None,
            "zaznamy": [],
            "platnost_k_datu": platnost,
            "isir_app_version": isir_version,
            "parser_health": "chyba",
            "parser_warnings": warnings
            + [
                "Nepodařilo se najít 'POČET NALEZENÝCH DLUŽNÍKŮ' v HTML — "
                "ISIR pravděpodobně změnil rozhraní."
            ],
        }

    if pocet == 0:
        return {
            "pocet_dluzniku": 0,
            "insolvence": False,
            "zaznamy": [],
            "platnost_k_datu": platnost,
            "isir_app_version": isir_version,
            "parser_health": "warn" if warnings else "ok",
            "parser_warnings": warnings,
        }

    # 3) Záznamy
    records = parse_records(text, warnings)

    # 4) Konzistentní validace: počet z hlavičky vs. počet vyparsovaných záznamů
    if len(records) != pocet:
        warnings.append(
            f"Nesoulad: hlavička hlásí {pocet} dlužníků, "
            f"vyparsovali jsme {len(records)} záznamů. "
            f"Pravděpodobně se změnil oddělovač záznamů (RECORD_SPLIT_RE)."
        )

    # Health rating
    if not records and pocet > 0:
        health = "chyba"
    elif warnings:
        health = "warn"
    else:
        health = "ok"

    return {
        "pocet_dluzniku": pocet,
        "insolvence": pocet > 0,
        "zaznamy": records,
        "platnost_k_datu": platnost,
        "isir_app_version": isir_version,
        "parser_health": health,
        "parser_warnings": warnings,
    }


def save_debug_dumps(ico: str, html: str, text: str) -> dict[str, str]:
    """Uloží raw HTML a cleaned text do data/raw/isir_html_dumps/.
    Vrací cesty (relativní k repo rootu) pro výstup.
    """
    repo_root = Path(__file__).resolve().parents[4]
    dump_dir = repo_root / DUMP_DIR_REL
    dump_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    html_path = dump_dir / f"{ico}_{ts}.html"
    txt_path = dump_dir / f"{ico}_{ts}.txt"
    try:
        html_path.write_text(html, encoding="utf-8")
        txt_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return {"_chyba_dumpu": str(exc)}

    return {
        "html": str(html_path.relative_to(repo_root)).replace("\\", "/"),
        "text": str(txt_path.relative_to(repo_root)).replace("\\", "/"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Insolvenční rejstřík (ISIR) lustrace")
    parser.add_argument("--ico", required=True)
    parser.add_argument(
        "--jen-aktualni",
        action="store_true",
        help="Omezit na aktuální (běžící) řízení místo všech historických.",
    )
    parser.add_argument(
        "--no-dump",
        action="store_true",
        help="Neukládat forenzní HTML/text dump ani při warning (jen pro testy).",
    )
    args = parser.parse_args()

    try:
        ico = normalize_ico(args.ico)
    except ValueError as exc:
        print(json.dumps({"chyba": str(exc)}, ensure_ascii=False))
        return 0

    try:
        url, html = fetch_isir(ico, args.jen_aktualni)
    except urllib.error.HTTPError as exc:
        print(
            json.dumps(
                {
                    "ico": ico,
                    "chyba": f"HTTP {exc.code}: {exc.reason}",
                    "parser_health": "chyba",
                    "parser_warnings": [],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ico": ico,
                    "chyba": f"{type(exc).__name__}: {exc}",
                    "parser_health": "chyba",
                    "parser_warnings": [],
                },
                ensure_ascii=False,
            )
        )
        return 0

    parsed = parse_response(html)
    debug_dumps = None
    if not args.no_dump and parsed.get("parser_health") in ("warn", "chyba"):
        debug_dumps = save_debug_dumps(ico, html, html_to_text(html))

    out = {
        "ico": ico,
        "isir_url": url,
        **parsed,
        "debug_dumps": debug_dumps,
        "chyba": None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

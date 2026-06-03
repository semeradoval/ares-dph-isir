#!/usr/bin/env python3
"""
ares_lookup.py — dotaz na ARES (obchodní + živnostenský rejstřík).

Používá veřejné REST API ministerstva financí:
  https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/

Dva režimy:

1) Detail podle IČO:
     python ares_lookup.py --ico 27074358
   Výstup: detail jednoho subjektu (viz níže).

2) Fulltext vyhledání podle názvu (když chybí IČO):
     python ares_lookup.py --nazev "ACME s.r.o." [--mesto "Brno"] [--limit 10]
   Výstup:
     {
       "hledano": { "nazev": "...", "mesto": "..." },
       "pocet": N,
       "kandidati": [ { "ico": "...", "nazev": "...", "sidlo": "...", "pravni_forma": "..." }, ... ]
     }

Detail režimu vrací:
  {
    "ico": "...",
    "nazev": "...",
    "pravni_forma": "...",
    "stav": "...",
    "datum_vzniku": "...",
    "sidlo": "...",
    "jednatele": [ { "jmeno": ..., "typ": "fyzicka|pravnicka", "funkce": ... } ],
    "zivnosti": [ ... ],
    "chyba": "...",        # přítomné pouze při chybě
    "raw": { ... }
  }

Kód vrací 0 i při chybě — výsledný JSON vždy popisuje stav.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


ARES_BASE_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"
ARES_SEARCH_URL = f"{ARES_BASE_URL}/vyhledat"
USER_AGENT = "sales-data-collection/1.0 (+https://github.com/dominikslechta1/sales_agent)"


def normalize_ico(ico: str) -> str:
    ico = ico.strip().replace(" ", "")
    if not ico.isdigit():
        raise ValueError(f"IČO musí být čistě číselné, dostal jsem: {ico!r}")
    return ico.zfill(8)


def fetch_ares(ico: str, timeout: float = 15.0) -> dict:
    url = f"{ARES_BASE_URL}/{ico}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


_LEGAL_SUFFIX_RE = __import__("re").compile(
    r"(,?\s*(spol\.?\s*s\s*r\.?\s*o\.?|s\.?\s*r\.?\s*o\.?|a\.?\s*s\.?|"
    r"k\.?\s*s\.?|v\.?\s*o\.?\s*s\.?|z\.?\s*s\.?|z\.?\s*ú\.?|"
    r"o\.?\s*s\.?|o\.?\s*p\.?\s*s\.?|se|sa))\s*$",
    __import__("re").IGNORECASE,
)


def _name_variants(nazev: str) -> list[str]:
    """Generuje postupně volnější varianty názvu pro ARES search.

    Příklady:
      "Autoservis Schwab – SDS s.r.o." → [
          "Autoservis Schwab – SDS s.r.o.",
          "Autoservis Schwab – SDS",
          "Autoservis Schwab",
          "Autoservis",
      ]

    ARES /vyhledat se chová jako prefix/substring match, ne jako fulltext —
    proto se vyplatí odříznout pravní formu i speciální znaky a postupně
    zkracovat.
    """
    variants: list[str] = []
    seen: set[str] = set()

    def push(v: str) -> None:
        v = v.strip(" -–—,;:\t")
        if v and v.lower() not in seen:
            seen.add(v.lower())
            variants.append(v)

    push(nazev)

    # odřízni pravní formu
    stripped = _LEGAL_SUFFIX_RE.sub("", nazev).strip(" -–—,;:")
    push(stripped)

    # rozdělení přes pomlčku / em-dash (kompoundní názvy typu "Jan Krobot - ISOOL, s.r.o.")
    import re as _re

    for part in _re.split(r"\s*[–—-]\s*", stripped):
        part = _LEGAL_SUFFIX_RE.sub("", part).strip()
        if part:
            push(part)

    # postupné ořezávání tail slov (pouze pro varianty bez suffixu)
    tokens = stripped.split()
    while len(tokens) > 1:
        tokens.pop()
        push(" ".join(tokens))

    return variants


def _search_once(nazev: str, mesto: str | None, limit: int, timeout: float) -> dict:
    body: dict = {
        "start": 0,
        "pocet": max(1, min(limit, 100)),
        "razeni": ["ico"],
        "obchodniJmeno": nazev,
    }
    if mesto:
        body["sidlo"] = {"textovaAdresa": mesto}

    req = urllib.request.Request(
        ARES_SEARCH_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_ares(
    nazev: str,
    mesto: str | None = None,
    limit: int = 10,
    timeout: float = 15.0,
) -> dict:
    """Fulltext vyhledání subjektů v ARES s automatickým fallbackem na varianty názvu.

    Vrací strukturu `{"ekonomickeSubjekty": [...], "pocetCelkem": N, "pouziteDotazy": [...]}`.
    Přidáno pole `pouziteDotazy` — seznam variant, které byly zkoušeny, a kolik kandidátů
    každá našla. Umožňuje volajícímu posoudit kvalitu shody.
    """
    attempts: list[dict] = []
    collected: list[dict] = []
    seen_icos: set[str] = set()

    variants = _name_variants(nazev)

    # 1) nejdřív zkusíme všechny varianty s městem (pokud je)
    # 2) pokud nenajdeme nic, zkusíme bez města
    rounds: list[str | None] = [mesto] if mesto else [None]
    if mesto:
        rounds.append(None)

    for city in rounds:
        for variant in variants:
            raw = _search_once(variant, city, limit, timeout)
            found = raw.get("ekonomickeSubjekty") or []
            attempts.append(
                {"nazev": variant, "mesto": city, "pocet": len(found)}
            )
            for s in found:
                ico = s.get("ico")
                if ico and ico not in seen_icos:
                    seen_icos.add(ico)
                    collected.append(s)
            if collected:
                break
        if collected:
            break

    return {
        "ekonomickeSubjekty": collected,
        "pocetCelkem": len(collected),
        "pouziteDotazy": attempts,
    }


def extract_candidate(raw: dict) -> dict:
    sidlo_obj = raw.get("sidlo") or {}
    sidlo_str = sidlo_obj.get("textovaAdresa") or ""
    mesto = sidlo_obj.get("nazevObce") or ""

    pravni_forma = ""
    pf = raw.get("pravniForma")
    if isinstance(pf, dict):
        pravni_forma = pf.get("nazev") or pf.get("kod") or ""
    elif isinstance(pf, str):
        pravni_forma = pf

    return {
        "ico": raw.get("ico"),
        "nazev": raw.get("obchodniJmeno") or raw.get("nazev"),
        "sidlo": sidlo_str,
        "mesto": mesto,
        "pravni_forma": pravni_forma,
        "datum_vzniku": raw.get("datumVzniku"),
    }


def extract(raw: dict) -> dict:
    sidlo_obj = raw.get("sidlo") or {}
    sidlo_str = sidlo_obj.get("textovaAdresa") or ""

    pravni_forma = ""
    pf = raw.get("pravniForma")
    if isinstance(pf, dict):
        pravni_forma = pf.get("nazev") or pf.get("kod") or ""
    elif isinstance(pf, str):
        pravni_forma = pf

    # Jednatelé / statutární orgán — ARES je má pod různými klíči
    # v různých subregistrech (obchodní rejstřík). Zde robustní extrakce.
    jednatele: list[dict] = []
    organy = (
        (raw.get("zaznamy") or [{}])[0]
        .get("statutarniOrgan", {})
        .get("clenoveStatutarnihoOrganu")
        or []
    )
    for clen in organy:
        fo = clen.get("fyzickaOsoba") or {}
        po = clen.get("pravnickaOsoba") or {}
        if fo:
            jmeno = " ".join(
                filter(
                    None,
                    [fo.get("jmeno"), fo.get("prijmeni")],
                )
            )
            jednatele.append(
                {
                    "jmeno": jmeno or None,
                    "typ": "fyzicka",
                    "funkce": clen.get("funkce", {}).get("nazev"),
                }
            )
        elif po:
            jednatele.append(
                {
                    "jmeno": po.get("obchodniJmeno") or None,
                    "ico": po.get("ico"),
                    "typ": "pravnicka",
                    "funkce": clen.get("funkce", {}).get("nazev"),
                }
            )

    return {
        "ico": raw.get("ico"),
        "nazev": raw.get("obchodniJmeno") or raw.get("nazev"),
        "pravni_forma": pravni_forma,
        "stav": (raw.get("czNace") and "aktivni") or "neznamy",
        "datum_vzniku": raw.get("datumVzniku"),
        "sidlo": sidlo_str,
        "jednatele": jednatele,
        "zivnosti": raw.get("czNace") or [],
    }


def run_detail(ico_arg: str) -> int:
    try:
        ico = normalize_ico(ico_arg)
    except ValueError as exc:
        print(json.dumps({"chyba": str(exc)}, ensure_ascii=False))
        return 0

    try:
        raw = fetch_ares(ico)
    except urllib.error.HTTPError as exc:
        print(
            json.dumps(
                {"ico": ico, "chyba": f"HTTP {exc.code}: {exc.reason}"},
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"ico": ico, "chyba": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 0

    extracted = extract(raw)
    extracted["raw"] = raw
    print(json.dumps(extracted, ensure_ascii=False, indent=2))
    return 0


def run_search(nazev: str, mesto: str | None, limit: int) -> int:
    try:
        raw = search_ares(nazev, mesto, limit=limit)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        print(
            json.dumps(
                {
                    "hledano": {"nazev": nazev, "mesto": mesto},
                    "chyba": f"HTTP {exc.code}: {exc.reason}",
                    "detail": body,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "hledano": {"nazev": nazev, "mesto": mesto},
                    "chyba": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return 0

    subjekty = raw.get("ekonomickeSubjekty") or []
    kandidati = [extract_candidate(s) for s in subjekty]
    out = {
        "hledano": {"nazev": nazev, "mesto": mesto},
        "pocet": raw.get("pocetCelkem", len(kandidati)),
        "pouzite_dotazy": raw.get("pouziteDotazy", []),
        "kandidati": kandidati,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARES lookup (detail podle IČO nebo fulltext podle názvu)")
    parser.add_argument("--ico", help="IČO pro detail subjektu")
    parser.add_argument("--nazev", help="Obchodní název pro fulltext vyhledání")
    parser.add_argument("--mesto", help="Upřesnění vyhledávání podle sídla")
    parser.add_argument("--limit", type=int, default=10, help="Max. počet kandidátů (default 10)")
    args = parser.parse_args()

    if not args.ico and not args.nazev:
        parser.error("zadej --ico NEBO --nazev")
    if args.ico and args.nazev:
        parser.error("--ico a --nazev jsou vzájemně výlučné")

    if args.ico:
        return run_detail(args.ico)
    return run_search(args.nazev, args.mesto, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
dph_check.py — ověření plátcovství DPH přes ARES.

Používá hlavní ARES endpoint `/ekonomicke-subjekty/{ico}` a extrahuje pole
`seznamRegistraci` — které obsahuje:

  stavZdrojeDph:    AKTIVNI / NEEXISTUJICI / ZANIKLY   → plátce DPH
  stavZdrojeSkDph:  AKTIVNI / NEEXISTUJICI / ZANIKLY   → skupinový plátce
  stavZdrojeRs:     ...                                 → obchodní rejstřík

**Pozn.:** ARES neposkytuje informaci o spolehlivosti plátce ani o
zveřejněných bankovních účtech — to nabízí jen veřejná služba ADIS MFČR
(SOAP). Pro tyto údaje vrací skript `null` a v poli `_todo` instrukci
pro případný fallback.

Vstup:
  --ico <IČO>

Výstup (stdout, JSON):
  {
    "ico": "...",
    "platce_dph": true|false|null,
    "skupinovy_platce_dph": true|false|null,
    "stav_zdroje_dph": "AKTIVNI|NEEXISTUJICI|ZANIKLY|...",
    "spolehlivy_platce": null,   # ARES neposkytuje
    "bankovni_ucty": [],         # ARES neposkytuje
    "_todo": "...",              # nápověda k fallbacku
    "chyba": "...",              # přítomné jen při chybě
    "raw": { ... }
  }
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"
USER_AGENT = "sales-data-collection/1.0 (+https://github.com/dominikslechta1/sales_agent)"


def normalize_ico(ico: str) -> str:
    ico = ico.strip().replace(" ", "")
    if not ico.isdigit():
        raise ValueError(f"IČO musí být číselné: {ico!r}")
    return ico.zfill(8)


def fetch(ico: str, timeout: float = 15.0) -> dict:
    url = f"{ARES_URL}/{ico}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _stav_to_bool(stav: str | None) -> bool | None:
    """Mapuje hodnotu `stavZdrojeDph` / `stavZdrojeSkDph` na bool.

    AKTIVNI       → True  (aktuálně registrovaný plátce)
    NEEXISTUJICI  → False (nikdy nebyl plátce)
    ZANIKLY       → False (byl plátce, ale registrace zanikla)
    ostatní       → None  (neznámý stav — raději nic než lhát)
    """
    if stav is None:
        return None
    s = stav.upper()
    if s == "AKTIVNI":
        return True
    if s in ("NEEXISTUJICI", "ZANIKLY"):
        return False
    return None


def extract(raw: dict) -> dict:
    reg = raw.get("seznamRegistraci") or {}
    stav_dph = reg.get("stavZdrojeDph")
    stav_sk = reg.get("stavZdrojeSkDph")

    return {
        "platce_dph": _stav_to_bool(stav_dph),
        "skupinovy_platce_dph": _stav_to_bool(stav_sk),
        "stav_zdroje_dph": stav_dph,
        "stav_zdroje_sk_dph": stav_sk,
        "spolehlivy_platce": None,
        "bankovni_ucty": [],
        "_todo": (
            "Spolehlivost plátce a zveřejněné bankovní účty ARES neposkytuje. "
            "Pro fallback volej ADIS SOAP službu Finanční správy "
            "(http://adisrws.mfcr.cz/adistc/DphReg) — vyžaduje SOAP klient."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="DPH check přes ARES")
    parser.add_argument("--ico", required=True)
    args = parser.parse_args()

    try:
        ico = normalize_ico(args.ico)
    except ValueError as exc:
        print(json.dumps({"chyba": str(exc)}, ensure_ascii=False))
        return 0

    try:
        raw = fetch(ico)
    except urllib.error.HTTPError as exc:
        # 404 na základním endpointu znamená "subjekt neexistuje", ne "neplátce"
        print(
            json.dumps(
                {
                    "ico": ico,
                    "chyba": f"HTTP {exc.code}: {exc.reason}",
                    "platce_dph": None,
                    "spolehlivy_platce": None,
                    "bankovni_ucty": [],
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
                    "platce_dph": None,
                    "spolehlivy_platce": None,
                    "bankovni_ucty": [],
                },
                ensure_ascii=False,
            )
        )
        return 0

    extracted = {"ico": ico, **extract(raw), "raw_seznam_registraci": raw.get("seznamRegistraci")}
    print(json.dumps(extracted, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

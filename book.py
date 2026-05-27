"""CLI per prenotare un posto studio sul portale easystaff (biblioteca di Mozzo).

Esempi:
    python book.py aree
    python book.py slot --giorno domani --sede piano1
    python book.py prenota --giorno domani --fascia mattina --sede piano1
    python book.py prenota --giorno 2026-05-29 --fascia 14:30 --sede narrativa --dry-run
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

BASE = "https://easyplanning.easystaff.it/portalePlanningNewAPI"
CLIENTE_SLUG = "mozzo-biblio"
CLIENTE_ID = 9
ENTRY_TYPE = 130            # "Prenotazione Posto studio"
DURATA_SECONDI = 10800      # 3h
FORM_KEY_COGNOME_NOME = "1611226175"
TZ = ZoneInfo("Europe/Rome")

# Alias sede → area_id (dalla risposta /api/aree/9). Estendibili con `book.py aree`.
SEDE_ALIAS = {
    "piano1": 67,
    "primopiano": 67,
    "primo-piano": 67,
    "narrativa": 71,
    "zona-narrativa": 71,
    "singole": 77,
    "postazioni-singole": 77,
}

GIORNI_SETTIMANA = {
    "lun": 0, "lunedi": 0, "lunedì": 0,
    "mar": 1, "martedi": 1, "martedì": 1,
    "mer": 2, "mercoledi": 2, "mercoledì": 2,
    "gio": 3, "giovedi": 3, "giovedì": 3,
    "ven": 4, "venerdi": 4, "venerdì": 4,
    "sab": 5, "sabato": 5,
    "dom": 6, "domenica": 6,
}


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=utf-8",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Referer": f"https://easyplanning.easystaff.it/portalePlanningNew/{CLIENTE_SLUG}",
        "Origin": "https://easyplanning.easystaff.it",
    })
    return s


def parse_giorno(s: str) -> date:
    s = s.strip().lower()
    today = datetime.now(TZ).date()
    if s == "oggi":
        return today
    if s == "domani":
        return today + timedelta(days=1)
    if s in ("dopodomani", "dopo-domani"):
        return today + timedelta(days=2)
    if s in GIORNI_SETTIMANA:
        target = GIORNI_SETTIMANA[s]
        diff = (target - today.weekday()) % 7
        if diff == 0:
            diff = 7
        return today + timedelta(days=diff)
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise SystemExit(f"Giorno non riconosciuto: {s!r}") from e


def parse_fascia(s: str) -> str:
    s = s.strip().lower()
    if s in ("mattina", "am", "mattino"):
        return "09:30"
    if s in ("pomeriggio", "pm"):
        return "14:30"
    if re.match(r"^\d{1,2}:\d{2}$", s):
        h, m = s.split(":")
        return f"{int(h):02d}:{m}"
    raise SystemExit(f"Fascia non riconosciuta: {s!r}")


def resolve_sede(s: str) -> int:
    s = s.strip().lower()
    if s in SEDE_ALIAS:
        return SEDE_ALIAS[s]
    if s.isdigit():
        return int(s)
    raise SystemExit(
        f"Sede non riconosciuta: {s!r}. Usa `book.py aree` per la lista, "
        f"oppure uno di: {', '.join(sorted(SEDE_ALIAS))}"
    )


def cmd_aree(session: requests.Session, _args) -> int:
    r = session.get(f"{BASE}/api/aree/{CLIENTE_ID}")
    r.raise_for_status()
    aree = r.json()["aree"]
    print(f"{'ID':>4}  {'NOME':40s}  CODE")
    print("-" * 80)
    for a in aree:
        print(f"{a['id']:>4}  {a['area_name'][:40]:40s}  {a['area_code']}")
    return 0


def cmd_slot(session: requests.Session, args) -> int:
    giorno = parse_giorno(args.giorno)
    area = resolve_sede(args.sede)
    url = f"{BASE}/api/entry/{ENTRY_TYPE}/schedule/{giorno.isoformat()}/{area}/{DURATA_SECONDI}"
    r = session.get(url)
    r.raise_for_status()
    payload = r.json().get("schedule", {}).get(giorno.isoformat(), {})
    if not payload:
        print(f"Nessuno slot per {giorno} sede {area}.")
        return 1
    print(f"Slot disponibili — {giorno} sede {area}:")
    for orario, info in payload.items():
        flag = "[OK]" if info["disponibili"] > 0 else "[--]"
        print(f"  {flag} {orario}  ({info['disponibili']}/{info['su']} liberi)")
    return 0


def cmd_prenota(session: requests.Session, args) -> int:
    giorno = parse_giorno(args.giorno)
    inizio_hhmm = parse_fascia(args.fascia)
    area = resolve_sede(args.sede)

    start_dt = datetime.combine(
        giorno, datetime.strptime(inizio_hhmm, "%H:%M").time(), tzinfo=TZ
    )
    end_dt = start_dt + timedelta(seconds=DURATA_SECONDI)

    # Verifica che lo slot esista davvero
    url_schedule = f"{BASE}/api/entry/{ENTRY_TYPE}/schedule/{giorno.isoformat()}/{area}/{DURATA_SECONDI}"
    r = session.get(url_schedule)
    r.raise_for_status()
    slots = r.json().get("schedule", {}).get(giorno.isoformat(), {})
    fine_hhmm = (start_dt + timedelta(seconds=DURATA_SECONDI)).strftime("%H:%M")
    slot_key = f"{inizio_hhmm}-{fine_hhmm}"
    if slot_key not in slots:
        print(f"Slot {slot_key} non trovato per {giorno} sede {area}.")
        print(f"Disponibili: {', '.join(slots.keys()) or '(nessuno)'}")
        return 1
    info = slots[slot_key]
    if info["disponibili"] == 0:
        print(f"Slot {slot_key} esaurito ({info['su']}/{info['su']} occupati).")
        return 1

    utente = {
        "codice_fiscale": os.environ["CODICE_FISCALE"],
        "email": os.environ["EMAIL"],
        "phone": os.environ["TELEFONO"],
    }
    cognome_nome = os.environ["COGNOME_NOME"]

    body = {
        "reservation_number": 0,
        "cliente": CLIENTE_SLUG,
        "start_time": int(start_dt.timestamp()),
        "end_time": int(end_dt.timestamp()),
        "durata": str(DURATA_SECONDI),
        "entry_type": ENTRY_TYPE,
        "area": area,
        "public_primary": utente["codice_fiscale"],
        "utente": utente,
        "servizio": {FORM_KEY_COGNOME_NOME: cognome_nome},
        "backoffice": {},
        "risorsa": None,
        "recaptchaToken": None,
        "timezone": "Europe/Rome",
    }

    print(f"-> {giorno} {slot_key}  sede {area}  ({info['disponibili']}/{info['su']} liberi)")
    print(f"  utente: {cognome_nome}  ({utente['codice_fiscale']})")

    if args.dry_run:
        print("DRY-RUN: nessuna chiamata di prenotazione effettuata.")
        return 0

    rs = session.post(f"{BASE}/api/entry/store", json=body)
    if not rs.ok:
        print(f"Errore store: HTTP {rs.status_code} — {rs.text[:300]}")
        return 2
    store_resp = rs.json()
    entry_id = store_resp["entry"]
    risorsa = store_resp.get("risorsa", {})
    codice = store_resp.get("codice_prenotazione", "?")
    print(f"  store ok → entry {entry_id}, codice {codice}, posto: {risorsa.get('resource_name')}")

    rc = session.post(f"{BASE}/api/entry/confirm/{entry_id}")
    if not rc.ok:
        print(f"Errore confirm: HTTP {rc.status_code} — {rc.text[:300]}")
        return 3
    print(f"[OK] Prenotazione confermata. Codice: {codice}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).parent / ".env")

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("aree", help="Lista delle sedi/aree disponibili")

    ps = sub.add_parser("slot", help="Mostra slot disponibili in un giorno")
    ps.add_argument("--giorno", required=True, help="oggi|domani|dopodomani|lun..dom|YYYY-MM-DD")
    ps.add_argument("--sede", default="piano1", help="Alias sede o ID area (default: piano1)")

    pp = sub.add_parser("prenota", help="Prenota uno slot")
    pp.add_argument("--giorno", required=True, help="oggi|domani|dopodomani|lun..dom|YYYY-MM-DD")
    pp.add_argument("--fascia", default="mattina", help="mattina|pomeriggio|HH:MM (default: mattina)")
    pp.add_argument("--sede", default="piano1", help="Alias sede o ID area (default: piano1)")
    pp.add_argument("--dry-run", action="store_true", help="Simula senza chiamare store/confirm")

    args = p.parse_args(argv)
    session = build_session()

    if args.cmd == "aree":
        return cmd_aree(session, args)
    if args.cmd == "slot":
        return cmd_slot(session, args)
    if args.cmd == "prenota":
        return cmd_prenota(session, args)
    return 1


if __name__ == "__main__":
    sys.exit(main())

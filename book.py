"""CLI e funzioni core per prenotare un posto studio sul portale easystaff (biblioteca di Mozzo).

Esempi CLI:
    python book.py aree
    python book.py slot --giorno domani --sede piano1
    python book.py prenota --giorno domani --fascia mattina --sede piano1
    python book.py prenota --giorno 2026-05-29 --fascia 14:30 --sede narrativa --dry-run

Le funzioni `lista_aree`, `slot_giorno`, `prenota_e_conferma` e `build_session`
sono importabili da altri moduli (es. bot.py).
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


class PrenotazioneError(Exception):
    """Errore atteso durante una prenotazione (slot esaurito, server in errore, ecc)."""


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


# ---------- core API ----------

def lista_aree(session: requests.Session) -> list[dict]:
    r = session.get(f"{BASE}/api/aree/{CLIENTE_ID}")
    r.raise_for_status()
    return r.json()["aree"]


def slot_giorno(session: requests.Session, giorno: date, area_id: int) -> dict[str, dict]:
    """Mappa 'HH:MM-HH:MM' -> {'disponibili': n, 'su': n, 'reserved': bool}.

    Ritorna dict vuoto se il giorno è chiuso o non disponibile.
    """
    url = f"{BASE}/api/entry/{ENTRY_TYPE}/schedule/{giorno.isoformat()}/{area_id}/{DURATA_SECONDI}"
    r = session.get(url)
    r.raise_for_status()
    sched = r.json().get("schedule", {})
    if not isinstance(sched, dict):
        return {}
    day = sched.get(giorno.isoformat())
    return day if isinstance(day, dict) else {}


def prenota_e_conferma(
    session: requests.Session,
    giorno: date,
    inizio_hhmm: str,
    area_id: int,
    utente: dict,
    cognome_nome: str,
    dry_run: bool = False,
) -> dict:
    """Esegue store + confirm. Ritorna dict con esito.

    utente = {'codice_fiscale': ..., 'email': ..., 'phone': ...}

    Ritorna:
        {'ok': True, 'codice': str, 'entry': int, 'postazione': str, 'slot': str}
        oppure
        {'ok': False, 'errore': str, 'slot': str}
    """
    start_dt = datetime.combine(
        giorno, datetime.strptime(inizio_hhmm, "%H:%M").time(), tzinfo=TZ
    )
    end_dt = start_dt + timedelta(seconds=DURATA_SECONDI)
    slot_key = f"{inizio_hhmm}-{end_dt.strftime('%H:%M')}"

    slots = slot_giorno(session, giorno, area_id)
    info = slots.get(slot_key)
    if not info:
        return {"ok": False, "slot": slot_key, "errore": f"Slot {slot_key} inesistente"}
    if info["disponibili"] == 0:
        return {"ok": False, "slot": slot_key, "errore": f"Slot {slot_key} esaurito (0/{info['su']})"}

    body = {
        "reservation_number": 0,
        "cliente": CLIENTE_SLUG,
        "start_time": int(start_dt.timestamp()),
        "end_time": int(end_dt.timestamp()),
        "durata": str(DURATA_SECONDI),
        "entry_type": ENTRY_TYPE,
        "area": area_id,
        "public_primary": utente["codice_fiscale"],
        "utente": utente,
        "servizio": {FORM_KEY_COGNOME_NOME: cognome_nome},
        "backoffice": {},
        "risorsa": None,
        "recaptchaToken": None,
        "timezone": "Europe/Rome",
    }

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "slot": slot_key,
            "codice": "(dry-run)",
            "entry": 0,
            "postazione": "(dry-run)",
        }

    rs = session.post(f"{BASE}/api/entry/store", json=body)
    if not rs.ok:
        return {"ok": False, "slot": slot_key, "errore": f"store HTTP {rs.status_code}: {rs.text[:200]}"}
    store_resp = rs.json()
    entry_id = store_resp["entry"]
    risorsa = (store_resp.get("risorsa") or {}).get("resource_name", "?")
    codice = store_resp.get("codice_prenotazione", "?")

    rc = session.post(f"{BASE}/api/entry/confirm/{entry_id}")
    if not rc.ok:
        return {"ok": False, "slot": slot_key, "errore": f"confirm HTTP {rc.status_code}: {rc.text[:200]}"}

    return {
        "ok": True,
        "slot": slot_key,
        "codice": codice,
        "entry": entry_id,
        "postazione": risorsa,
    }


def utente_da_env() -> tuple[dict, str]:
    """Estrae utente e cognome_nome dalle variabili d'ambiente (.env già caricato)."""
    utente = {
        "codice_fiscale": os.environ["CODICE_FISCALE"],
        "email": os.environ["EMAIL"],
        "phone": os.environ["TELEFONO"],
    }
    cognome_nome = os.environ["COGNOME_NOME"]
    return utente, cognome_nome


# ---------- parsing CLI ----------

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


# ---------- CLI commands ----------

def cmd_aree(session: requests.Session, _args) -> int:
    aree = lista_aree(session)
    print(f"{'ID':>4}  {'NOME':40s}  CODE")
    print("-" * 80)
    for a in aree:
        print(f"{a['id']:>4}  {a['area_name'][:40]:40s}  {a['area_code']}")
    return 0


def cmd_slot(session: requests.Session, args) -> int:
    giorno = parse_giorno(args.giorno)
    area = resolve_sede(args.sede)
    slots = slot_giorno(session, giorno, area)
    if not slots:
        print(f"Nessuno slot per {giorno} sede {area}.")
        return 1
    print(f"Slot disponibili — {giorno} sede {area}:")
    for orario, info in slots.items():
        flag = "[OK]" if info["disponibili"] > 0 else "[--]"
        print(f"  {flag} {orario}  ({info['disponibili']}/{info['su']} liberi)")
    return 0


def cmd_prenota(session: requests.Session, args) -> int:
    giorno = parse_giorno(args.giorno)
    inizio_hhmm = parse_fascia(args.fascia)
    area = resolve_sede(args.sede)
    utente, cognome_nome = utente_da_env()

    print(f"-> {giorno} {inizio_hhmm}+3h  sede {area}  utente: {cognome_nome} ({utente['codice_fiscale']})")
    if args.dry_run:
        print("DRY-RUN: nessuna chiamata di prenotazione effettuata.")

    res = prenota_e_conferma(session, giorno, inizio_hhmm, area, utente, cognome_nome, dry_run=args.dry_run)
    if not res["ok"]:
        print(f"Errore: {res['errore']}")
        return 2
    if res.get("dry_run"):
        return 0
    print(f"  store ok -> entry {res['entry']}, posto: {res['postazione']}")
    print(f"[OK] Prenotazione confermata. Codice: {res['codice']}")
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

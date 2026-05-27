"""Bot Telegram per AutomaticMozzoLibrary.

Comandi:
    /start      schermata iniziale con scorciatoie
    /prenota    wizard di prenotazione
    /domattina  prenota subito mattina al piano 1 di domani
    /slot       disponibilità prossimi giorni

Configurazione (.env):
    TELEGRAM_BOT_TOKEN          token di BotFather
    TELEGRAM_ALLOWED_CHAT_IDS   chat_id autorizzati, separati da virgola
    CODICE_FISCALE / EMAIL / TELEFONO / COGNOME_NOME   dati per la prenotazione

Avvio:
    python bot.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from book import (
    TZ,
    build_session,
    prenota_e_conferma,
    slot_giorno,
    utente_da_env,
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
log = logging.getLogger("autobiblio.bot")
logging.getLogger("httpx").setLevel(logging.WARNING)

SEDI = [
    (67, "Posto Studio 1° Piano"),
    (71, "Zona Narrativa"),
]
FASCE = [
    ("09:30", "Mattina (9:30-12:30)"),
    ("14:30", "Pomeriggio (14:30-17:30)"),
]
GIORNI_BREVE = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
MAX_GIORNI_AVANTI = 7


# ---------- auth ----------

def parse_allowed(raw: str) -> set[int]:
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def is_authorized(update: Update, allowed: set[int]) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id in allowed


async def reject_unauthorized(update: Update, allowed: set[int]) -> bool:
    if is_authorized(update, allowed):
        return False
    chat_id = update.effective_chat.id if update.effective_chat else "?"
    user = update.effective_user.username if update.effective_user else "?"
    log.warning("Unauthorized chat_id=%s user=%s", chat_id, user)
    if update.message:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
    elif update.callback_query:
        await update.callback_query.answer("Non autorizzato.", show_alert=True)
    return True


# ---------- helpers GUI ----------

def label_giorno(d: date) -> str:
    today = datetime.now(TZ).date()
    if d == today:
        return "Oggi"
    if d == today + timedelta(days=1):
        return "Domani"
    return f"{GIORNI_BREVE[d.weekday()]} {d.day}/{d.month}"


def nome_sede(area_id: int) -> str:
    for aid, nome in SEDI:
        if aid == area_id:
            return nome
    return f"sede {area_id}"


def label_fascia(hhmm: str) -> str:
    for h, nome in FASCE:
        if h == hhmm:
            return nome
    return hhmm


def kb_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Domattina, Piano 1", callback_data="quick:domattina-piano1")],
        [InlineKeyboardButton("📅 Nuova prenotazione", callback_data="wiz:sede")],
        [InlineKeyboardButton("🔍 Slot disponibili", callback_data="slot:home")],
    ])


def kb_sedi() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(nome, callback_data=f"wiz:giorno:{aid}")] for aid, nome in SEDI]
    rows.append([InlineKeyboardButton("⬅️ Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def kb_back(target: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("⬅️ Indietro", callback_data=target)]


# ---------- disponibilità (sync wrappers chiamati da to_thread) ----------

def _giorni_con_disponibilita(session, area_id: int) -> list[tuple[date, dict]]:
    today = datetime.now(TZ).date()
    out = []
    for off in range(MAX_GIORNI_AVANTI):
        d = today + timedelta(days=off)
        slots = slot_giorno(session, d, area_id)
        if any(s["disponibili"] > 0 for s in slots.values()):
            out.append((d, slots))
    return out


def _fasce_disponibili(session, area_id: int, giorno: date) -> dict[str, dict]:
    return slot_giorno(session, giorno, area_id)


# ---------- handlers ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await reject_unauthorized(update, context.application.bot_data["allowed"]):
        return
    await update.message.reply_text(
        "Cosa vuoi fare?",
        reply_markup=kb_home(),
    )


async def cmd_prenota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await reject_unauthorized(update, context.application.bot_data["allowed"]):
        return
    await update.message.reply_text(
        "Dove vuoi prenotare?",
        reply_markup=kb_sedi(),
    )


async def cmd_domattina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await reject_unauthorized(update, context.application.bot_data["allowed"]):
        return
    await _avvia_quick(update, context, area_id=67, fascia="09:30", giorno=datetime.now(TZ).date() + timedelta(days=1))


async def cmd_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await reject_unauthorized(update, context.application.bot_data["allowed"]):
        return
    await _mostra_slot_overview(update.message, context)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await reject_unauthorized(update, context.application.bot_data["allowed"]):
        return
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "home":
        await q.edit_message_text("Cosa vuoi fare?", reply_markup=kb_home())
        return

    if data == "wiz:sede":
        await q.edit_message_text("Dove vuoi prenotare?", reply_markup=kb_sedi())
        return

    if data.startswith("wiz:giorno:"):
        area_id = int(data.split(":")[2])
        context.user_data["area_id"] = area_id
        await _mostra_giorni(q, context, area_id)
        return

    if data.startswith("wiz:fascia:"):
        _, _, area_id_s, iso = data.split(":", 3)
        area_id = int(area_id_s)
        giorno = date.fromisoformat(iso)
        context.user_data["area_id"] = area_id
        context.user_data["giorno"] = iso
        await _mostra_fasce(q, context, area_id, giorno)
        return

    if data.startswith("wiz:riepilogo:"):
        _, _, area_id_s, iso, hhmm = data.split(":", 4)
        area_id = int(area_id_s)
        giorno = date.fromisoformat(iso)
        context.user_data["area_id"] = area_id
        context.user_data["giorno"] = iso
        context.user_data["fascia"] = hhmm
        await _mostra_riepilogo(q, context, area_id, giorno, hhmm)
        return

    if data == "wiz:confirm":
        await _esegui_prenotazione(q, context)
        return

    if data == "wiz:annulla":
        await q.edit_message_text("Annullato.", reply_markup=kb_home())
        return

    if data == "quick:domattina-piano1":
        giorno = datetime.now(TZ).date() + timedelta(days=1)
        await _avvia_quick(update, context, area_id=67, fascia="09:30", giorno=giorno)
        return

    if data == "slot:home":
        await _mostra_slot_overview(q.message, context, edit=True)
        return

    log.warning("Callback data sconosciuta: %r", data)


# ---------- step wizard ----------

async def _mostra_giorni(q, context, area_id: int):
    await q.edit_message_text(f"Caricamento giorni disponibili per {nome_sede(area_id)}...")
    session = context.application.bot_data["session"]
    giorni = await asyncio.to_thread(_giorni_con_disponibilita, session, area_id)
    if not giorni:
        await q.edit_message_text(
            f"Nessun giorno con slot liberi nei prossimi {MAX_GIORNI_AVANTI} giorni.",
            reply_markup=InlineKeyboardMarkup([kb_back("wiz:sede")]),
        )
        return
    rows = []
    for d, slots in giorni:
        mattina = slots.get("09:30-12:30")
        pom = slots.get("14:30-17:30")
        badge_m = f"M:{mattina['disponibili']}" if mattina and mattina["disponibili"] > 0 else "--"
        badge_p = f"P:{pom['disponibili']}" if pom and pom["disponibili"] > 0 else "--"
        rows.append([InlineKeyboardButton(
            f"{label_giorno(d)}  ({badge_m} {badge_p})",
            callback_data=f"wiz:fascia:{area_id}:{d.isoformat()}",
        )])
    rows.append(kb_back("wiz:sede"))
    await q.edit_message_text(
        f"Sede: {nome_sede(area_id)}\nScegli il giorno:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _mostra_fasce(q, context, area_id: int, giorno: date):
    await q.edit_message_text("Caricamento fasce...")
    session = context.application.bot_data["session"]
    slots = await asyncio.to_thread(_fasce_disponibili, session, area_id, giorno)
    rows = []
    for hhmm, etichetta in FASCE:
        end_h = (datetime.strptime(hhmm, "%H:%M") + timedelta(hours=3)).strftime("%H:%M")
        info = slots.get(f"{hhmm}-{end_h}")
        if not info or info["disponibili"] == 0:
            continue
        rows.append([InlineKeyboardButton(
            f"{etichetta} — {info['disponibili']}/{info['su']} liberi",
            callback_data=f"wiz:riepilogo:{area_id}:{giorno.isoformat()}:{hhmm}",
        )])
    if not rows:
        rows.append([InlineKeyboardButton("(nessuna fascia 3h libera)", callback_data="noop")])
    rows.append(kb_back(f"wiz:giorno:{area_id}"))
    await q.edit_message_text(
        f"Sede: {nome_sede(area_id)}\nGiorno: {label_giorno(giorno)} ({giorno.isoformat()})\nFascia:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _mostra_riepilogo(q, context, area_id: int, giorno: date, hhmm: str):
    testo = (
        "Stai per prenotare:\n\n"
        f"📍 Sede: {nome_sede(area_id)}\n"
        f"📅 Giorno: {label_giorno(giorno)} ({giorno.isoformat()})\n"
        f"🕐 Fascia: {label_fascia(hhmm)}\n\n"
        "Confermi?"
    )
    rows = [
        [
            InlineKeyboardButton("✓ Prenota", callback_data="wiz:confirm"),
            InlineKeyboardButton("✗ Annulla", callback_data="wiz:annulla"),
        ],
    ]
    await q.edit_message_text(testo, reply_markup=InlineKeyboardMarkup(rows))


async def _esegui_prenotazione(q, context):
    area_id = context.user_data.get("area_id")
    iso = context.user_data.get("giorno")
    hhmm = context.user_data.get("fascia")
    if not (area_id and iso and hhmm):
        await q.edit_message_text("Stato perso. Ricomincia con /prenota.", reply_markup=kb_home())
        return
    giorno = date.fromisoformat(iso)
    await q.edit_message_text("Prenoto...")
    session = context.application.bot_data["session"]
    utente, cognome_nome = context.application.bot_data["utente"]
    res = await asyncio.to_thread(
        prenota_e_conferma, session, giorno, hhmm, area_id, utente, cognome_nome, False
    )
    if not res["ok"]:
        await q.edit_message_text(
            f"❌ Errore: {res['errore']}",
            reply_markup=kb_home(),
        )
        return
    testo = (
        "✅ Prenotazione confermata.\n\n"
        f"📍 {nome_sede(area_id)}\n"
        f"📅 {label_giorno(giorno)} {giorno.isoformat()}\n"
        f"🕐 {res['slot']}\n"
        f"🪑 {res['postazione']}\n"
        f"🎫 Codice: {res['codice']}"
    )
    await q.edit_message_text(testo, reply_markup=kb_home())
    context.user_data.clear()


# ---------- shortcut "domattina" ----------

async def _avvia_quick(update: Update, context: ContextTypes.DEFAULT_TYPE, area_id: int, fascia: str, giorno: date):
    """Riepilogo immediato per scorciatoia: salta sede/giorno/fascia."""
    context.user_data["area_id"] = area_id
    context.user_data["giorno"] = giorno.isoformat()
    context.user_data["fascia"] = fascia

    end_h = (datetime.strptime(fascia, "%H:%M") + timedelta(hours=3)).strftime("%H:%M")
    slot_key = f"{fascia}-{end_h}"
    session = context.application.bot_data["session"]
    slots = await asyncio.to_thread(slot_giorno, session, giorno, area_id)
    info = slots.get(slot_key)
    if not info or info["disponibili"] == 0:
        msg = (
            f"❌ Slot {slot_key} non disponibile per {label_giorno(giorno)} a {nome_sede(area_id)}."
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=kb_home())
        else:
            await update.message.reply_text(msg, reply_markup=kb_home())
        return

    testo = (
        f"⚡ Scorciatoia\n\n"
        f"📍 {nome_sede(area_id)}\n"
        f"📅 {label_giorno(giorno)} ({giorno.isoformat()})\n"
        f"🕐 {label_fascia(fascia)} — {info['disponibili']}/{info['su']} liberi\n\n"
        "Confermi?"
    )
    rows = [[
        InlineKeyboardButton("✓ Prenota", callback_data="wiz:confirm"),
        InlineKeyboardButton("✗ Annulla", callback_data="wiz:annulla"),
    ]]
    markup = InlineKeyboardMarkup(rows)
    if update.callback_query:
        await update.callback_query.edit_message_text(testo, reply_markup=markup)
    else:
        await update.message.reply_text(testo, reply_markup=markup)


# ---------- overview slot ----------

async def _mostra_slot_overview(target, context, edit: bool = False):
    session = context.application.bot_data["session"]
    righe = []
    for area_id, nome in SEDI:
        giorni = await asyncio.to_thread(_giorni_con_disponibilita, session, area_id)
        righe.append(f"\n*{nome}*")
        if not giorni:
            righe.append("  (nessuno slot libero)")
            continue
        for d, slots in giorni:
            m = slots.get("09:30-12:30")
            p = slots.get("14:30-17:30")
            badge_m = f"M {m['disponibili']}/{m['su']}" if m else "--"
            badge_p = f"P {p['disponibili']}/{p['su']}" if p else "--"
            righe.append(f"  {label_giorno(d)} ({d.isoformat()}): {badge_m}  {badge_p}")
    testo = "Slot disponibili prossimi giorni:\n" + "\n".join(righe)
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Home", callback_data="home")]])
    if edit:
        await target.edit_text(testo, reply_markup=markup, parse_mode="Markdown")
    else:
        await target.reply_text(testo, reply_markup=markup, parse_mode="Markdown")


# ---------- bootstrap ----------

async def _post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("prenota", "Nuova prenotazione (wizard)"),
        BotCommand("domattina", "Prenota domattina al Piano 1"),
        BotCommand("slot", "Disponibilità prossimi giorni"),
        BotCommand("start", "Schermata iniziale"),
    ])
    log.info("Comandi bot registrati. Bot avviato.")


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("TELEGRAM_BOT_TOKEN non impostato nel .env")
        return 1
    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed = parse_allowed(allowed_raw)
    if not allowed:
        log.error("TELEGRAM_ALLOWED_CHAT_IDS vuoto o non valido")
        return 1

    utente, cognome_nome = utente_da_env()
    session = build_session()

    app = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .build()
    )
    app.bot_data["allowed"] = allowed
    app.bot_data["session"] = session
    app.bot_data["utente"] = (utente, cognome_nome)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("prenota", cmd_prenota))
    app.add_handler(CommandHandler("domattina", cmd_domattina))
    app.add_handler(CommandHandler("slot", cmd_slot))
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info("Whitelist chat_ids: %s", allowed)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())

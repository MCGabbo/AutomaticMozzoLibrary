"""Bot Telegram per AutomaticMozzoLibrary — multi-utente.

Comandi utente:
    /start              schermata iniziale
    /registra           registra il proprio profilo (wizard)
    /profilo            mostra il proprio profilo
    /cancella_profilo   elimina i propri dati dal bot
    /prenota            wizard di prenotazione
    /domattina          shortcut: mattina al Piano 1 di domani
    /slot               disponibilità prossimi giorni

Comandi admin (solo TELEGRAM_ADMIN_CHAT_IDS):
    /admin_utenti       lista utenti registrati con stato

Configurazione (.env):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_ADMIN_CHAT_IDS    chat_id admin separati da virgola
                              (fallback: TELEGRAM_ALLOWED_CHAT_IDS)
    CODICE_FISCALE / EMAIL / TELEFONO / COGNOME_NOME
                              dati admin per il bootstrap automatico al
                              primo avvio (opzionali se l'admin è già in DB)
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
    ConversationHandler,
    MessageHandler,
    filters,
)

import users
from book import (
    TZ,
    build_session,
    prenota_e_conferma,
    slot_giorno,
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

# Stati del ConversationHandler /registra
REG_CF, REG_EMAIL, REG_PHONE, REG_NOME, REG_CONFIRM = range(5)


# ---------- auth helpers ----------

def parse_chat_ids(raw: str) -> set[int]:
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def is_admin(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return chat_id in context.application.bot_data["admins"]


def is_authorized(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return is_admin(chat_id, context) or users.is_approved(chat_id)


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True se NON autorizzato (e ha risposto col messaggio appropriato)."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return True
    if is_authorized(chat_id, context):
        return False
    u = users.get_user(chat_id)
    if u and u.status == users.STATUS_PENDING:
        msg = (
            "⏳ La tua registrazione è in attesa di approvazione dall'admin. "
            "Ti scrivo io quando è approvata."
        )
    elif u and u.status == users.STATUS_BANNED:
        msg = "❌ Il tuo profilo è stato sospeso."
    else:
        msg = (
            "Non sei registrato. Usa /registra per fornire i tuoi dati e "
            "richiedere l'accesso."
        )
    if update.message:
        await update.message.reply_text(msg)
    elif update.callback_query:
        await update.callback_query.answer(msg, show_alert=True)
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


# ---------- API wrappers (chiamati da to_thread) ----------

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


# ---------- comandi base ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_authorized(chat_id, context):
        await update.message.reply_text("Cosa vuoi fare?", reply_markup=kb_home())
        return
    u = users.get_user(chat_id)
    if u and u.status == users.STATUS_PENDING:
        await update.message.reply_text(
            "⏳ Registrazione in attesa di approvazione. Ti scrivo quando approvata."
        )
        return
    await update.message.reply_text(
        "Ciao! Questo bot serve per prenotare un posto studio alla Biblioteca di Mozzo.\n\n"
        "Per usarlo devi prima registrarti con /registra: ti chiederò i dati che il "
        "portale richiede al momento della prenotazione (codice fiscale, email, telefono, "
        "cognome e nome). Quando avrai compilato, l'admin riceverà una notifica per "
        "approvare il tuo accesso."
    )


async def cmd_prenota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update, context):
        return
    await update.message.reply_text("Dove vuoi prenotare?", reply_markup=kb_sedi())


async def cmd_domattina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update, context):
        return
    giorno = datetime.now(TZ).date() + timedelta(days=1)
    await _avvia_quick(update, context, area_id=67, fascia="09:30", giorno=giorno)


async def cmd_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update, context):
        return
    await _mostra_slot_overview(update.message, context)


async def cmd_profilo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = users.get_user(chat_id)
    if not u:
        await update.message.reply_text(
            "Non hai un profilo. Usa /registra per crearne uno."
        )
        return
    status_label = {
        users.STATUS_APPROVED: "✅ approvato",
        users.STATUS_PENDING: "⏳ in attesa",
        users.STATUS_BANNED: "❌ sospeso",
    }.get(u.status, u.status)
    await update.message.reply_text(
        f"*Il tuo profilo*\n\n"
        f"👤 {u.cognome_nome}\n"
        f"🆔 {u.codice_fiscale}\n"
        f"📧 {u.email}\n"
        f"📱 {u.telefono}\n"
        f"Stato: {status_label}\n\n"
        f"Per modificare i dati: /registra (re-invia il modulo).\n"
        f"Per cancellare i dati: /cancella_profilo.",
        parse_mode="Markdown",
    )


async def cmd_cancella_profilo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = users.get_user(chat_id)
    if not u:
        await update.message.reply_text("Non hai un profilo da cancellare.")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Sì, cancella", callback_data="profilo:delete_yes"),
        InlineKeyboardButton("✗ Annulla", callback_data="profilo:delete_no"),
    ]])
    await update.message.reply_text(
        "Sei sicuro di voler cancellare il tuo profilo dal bot?\n"
        "(Le prenotazioni già confermate sul portale NON vengono toccate: "
        "se vuoi disdirle, fallo dalla pagina 'Gestisci prenotazione' del portale.)",
        reply_markup=kb,
    )


# ---------- registrazione (ConversationHandler) ----------

async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = users.get_user(chat_id)
    if u and u.status == users.STATUS_APPROVED:
        await update.message.reply_text(
            "Sei già registrato e approvato. Se vuoi modificare i dati prosegui: "
            "verrà richiesta di nuovo l'approvazione dell'admin.\n\n"
            "Per uscire: /annulla"
        )
    elif u and u.status == users.STATUS_BANNED:
        await update.message.reply_text(
            "Il tuo profilo è sospeso. Contatta l'admin."
        )
        return ConversationHandler.END
    context.user_data["reg"] = {}
    await update.message.reply_text(
        "📝 Registrazione (4 passi). In qualsiasi momento /annulla per uscire.\n\n"
        "1/4 — Mandami il tuo *codice fiscale* (16 caratteri).",
        parse_mode="Markdown",
    )
    return REG_CF


async def reg_cf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cf = users.validate_cf(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}\nRiprova:")
        return REG_CF
    context.user_data["reg"]["cf"] = cf
    await update.message.reply_text("2/4 — Mandami la tua *email*.", parse_mode="Markdown")
    return REG_EMAIL


async def reg_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        email = users.validate_email(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}\nRiprova:")
        return REG_EMAIL
    context.user_data["reg"]["email"] = email
    await update.message.reply_text("3/4 — Mandami il tuo *numero di telefono*.", parse_mode="Markdown")
    return REG_PHONE


async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        phone = users.validate_phone(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}\nRiprova:")
        return REG_PHONE
    context.user_data["reg"]["phone"] = phone
    await update.message.reply_text(
        "4/4 — Mandami *cognome e nome* (in quest'ordine, separati da spazio).",
        parse_mode="Markdown",
    )
    return REG_NOME


async def reg_nome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        nome = users.validate_nome(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}\nRiprova:")
        return REG_NOME
    context.user_data["reg"]["nome"] = nome
    d = context.user_data["reg"]
    testo = (
        "Controlla i dati:\n\n"
        f"🆔 {d['cf']}\n"
        f"📧 {d['email']}\n"
        f"📱 {d['phone']}\n"
        f"👤 {d['nome']}\n\n"
        "Confermi l'invio all'admin per l'approvazione?"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Invia", callback_data="reg:submit"),
        InlineKeyboardButton("✗ Annulla", callback_data="reg:cancel"),
    ]])
    await update.message.reply_text(testo, reply_markup=kb)
    return REG_CONFIRM


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("reg", None)
    if update.message:
        await update.message.reply_text("Registrazione annullata.")
    return ConversationHandler.END


async def reg_submit_from_callback(q, context: ContextTypes.DEFAULT_TYPE):
    chat_id = q.from_user.id
    username = q.from_user.username
    d = context.user_data.get("reg")
    if not d:
        await q.edit_message_text("Stato perso. Ricomincia con /registra.")
        return ConversationHandler.END
    users.upsert_pending(chat_id, d["cf"], d["email"], d["phone"], d["nome"], username)
    await q.edit_message_text(
        "✅ Richiesta inviata. Ti scrivo io quando l'admin approva."
    )
    # Notifica admin
    nome = d["nome"]
    cf = d["cf"]
    msg = (
        f"🆕 Nuova registrazione:\n\n"
        f"👤 {nome}\n"
        f"🆔 {cf}\n"
        f"📧 {d['email']}\n"
        f"📱 {d['phone']}\n"
        f"👤 Telegram: @{username or '(nessun username)'}  ({chat_id})\n\n"
        f"Approva?"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Approva", callback_data=f"admin:approve:{chat_id}"),
        InlineKeyboardButton("✗ Rifiuta", callback_data=f"admin:reject:{chat_id}"),
    ]])
    for admin_id in context.application.bot_data["admins"]:
        try:
            await context.bot.send_message(admin_id, msg, reply_markup=kb)
        except Exception as e:
            log.warning("Impossibile notificare admin %s: %s", admin_id, e)
    context.user_data.pop("reg", None)
    return ConversationHandler.END


# ---------- admin ----------

async def cmd_admin_utenti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id, context):
        await update.message.reply_text("Solo admin.")
        return
    lista = users.list_all()
    if not lista:
        await update.message.reply_text("Nessun utente registrato.")
        return
    icons = {
        users.STATUS_APPROVED: "✅",
        users.STATUS_PENDING: "⏳",
        users.STATUS_BANNED: "❌",
    }
    righe = ["*Utenti registrati:*\n"]
    for u in lista:
        uname = f"@{u.telegram_username}" if u.telegram_username else "(no @)"
        righe.append(
            f"{icons.get(u.status, '?')} `{u.chat_id}` — {u.cognome_nome} {uname}"
        )
    await update.message.reply_text("\n".join(righe), parse_mode="Markdown")


# ---------- callback dispatcher ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""

    # --- registrazione (sempre permessa) ---
    if data == "reg:submit":
        await q.answer()
        return await reg_submit_from_callback(q, context)
    if data == "reg:cancel":
        await q.answer()
        context.user_data.pop("reg", None)
        await q.edit_message_text("Registrazione annullata.")
        return ConversationHandler.END

    # --- profilo (l'utente cancella se stesso, anche da pending/banned) ---
    if data == "profilo:delete_yes":
        await q.answer()
        users.delete(q.from_user.id)
        await q.edit_message_text("✅ Profilo cancellato.")
        return
    if data == "profilo:delete_no":
        await q.answer()
        await q.edit_message_text("Annullato.")
        return

    # --- admin ---
    if data.startswith("admin:"):
        if not is_admin(q.from_user.id, context):
            await q.answer("Solo admin.", show_alert=True)
            return
        _, action, target_s = data.split(":", 2)
        target_id = int(target_s)
        target_user = users.get_user(target_id)
        if not target_user:
            await q.answer("Utente non più presente.", show_alert=True)
            await q.edit_message_reply_markup(reply_markup=None)
            return
        if action == "approve":
            users.approve(target_id)
            await q.edit_message_text(f"✅ Approvato: {target_user.cognome_nome} ({target_id})")
            try:
                await context.bot.send_message(
                    target_id,
                    "✅ Sei stato approvato! Ora puoi usare /prenota, /domattina, /slot.",
                    reply_markup=kb_home(),
                )
            except Exception as e:
                log.warning("Notifica approvazione fallita per %s: %s", target_id, e)
            await q.answer("Approvato.")
            return
        if action == "reject":
            users.delete(target_id)
            await q.edit_message_text(f"❌ Rifiutato: {target_user.cognome_nome} ({target_id})")
            try:
                await context.bot.send_message(
                    target_id,
                    "❌ La tua registrazione è stata rifiutata. Contatta l'admin se pensi sia un errore.",
                )
            except Exception:
                pass
            await q.answer("Rifiutato.")
            return

    # --- da qui in giù serve essere autorizzati ---
    if await guard(update, context):
        return
    await q.answer()

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


# ---------- step wizard prenotazione ----------

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
    rows = [[
        InlineKeyboardButton("✓ Prenota", callback_data="wiz:confirm"),
        InlineKeyboardButton("✗ Annulla", callback_data="wiz:annulla"),
    ]]
    await q.edit_message_text(testo, reply_markup=InlineKeyboardMarkup(rows))


async def _esegui_prenotazione(q, context):
    area_id = context.user_data.get("area_id")
    iso = context.user_data.get("giorno")
    hhmm = context.user_data.get("fascia")
    if not (area_id and iso and hhmm):
        await q.edit_message_text("Stato perso. Ricomincia con /prenota.", reply_markup=kb_home())
        return
    giorno = date.fromisoformat(iso)
    payload = users.booking_payload(q.from_user.id)
    if not payload:
        await q.edit_message_text(
            "Il tuo profilo non risulta più approvato. Verifica con /profilo.",
        )
        return
    utente, cognome_nome = payload
    await q.edit_message_text("Prenoto...")
    session = context.application.bot_data["session"]
    res = await asyncio.to_thread(
        prenota_e_conferma, session, giorno, hhmm, area_id, utente, cognome_nome, False
    )
    if not res["ok"]:
        await q.edit_message_text(f"❌ Errore: {res['errore']}", reply_markup=kb_home())
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
    for k in ("area_id", "giorno", "fascia"):
        context.user_data.pop(k, None)


# ---------- shortcut domattina ----------

async def _avvia_quick(update: Update, context: ContextTypes.DEFAULT_TYPE, area_id: int, fascia: str, giorno: date):
    context.user_data["area_id"] = area_id
    context.user_data["giorno"] = giorno.isoformat()
    context.user_data["fascia"] = fascia

    end_h = (datetime.strptime(fascia, "%H:%M") + timedelta(hours=3)).strftime("%H:%M")
    slot_key = f"{fascia}-{end_h}"
    session = context.application.bot_data["session"]
    slots = await asyncio.to_thread(slot_giorno, session, giorno, area_id)
    info = slots.get(slot_key)
    if not info or info["disponibili"] == 0:
        msg = f"❌ Slot {slot_key} non disponibile per {label_giorno(giorno)} a {nome_sede(area_id)}."
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
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Prenota", callback_data="wiz:confirm"),
        InlineKeyboardButton("✗ Annulla", callback_data="wiz:annulla"),
    ]])
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

def bootstrap_admin_from_env(admins: set[int]) -> None:
    """Se l'admin non è ancora nel DB e ci sono i 4 campi nel .env, lo crea approved."""
    cf = os.environ.get("CODICE_FISCALE")
    email = os.environ.get("EMAIL")
    phone = os.environ.get("TELEFONO")
    nome = os.environ.get("COGNOME_NOME")
    if not (cf and email and phone and nome):
        return
    try:
        cf_n = users.validate_cf(cf)
        email_n = users.validate_email(email)
        phone_n = users.validate_phone(phone)
        nome_n = users.validate_nome(nome)
    except ValueError as e:
        log.warning("Bootstrap admin: dati .env non validi: %s", e)
        return
    for admin_id in admins:
        if not users.get_user(admin_id):
            users.upsert_approved(admin_id, cf_n, email_n, phone_n, nome_n, None)
            log.info("Admin %s bootstrappato nel DB da .env", admin_id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Logga in modo conciso e prova a dare feedback all'utente."""
    err = context.error
    log.error("Update error: %s: %s", type(err).__name__, err)
    if not isinstance(update, Update):
        return
    msg = "⚠️ Errore temporaneo (connessione al portale). Riprova tra qualche secondo."
    try:
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        elif update.message:
            await update.message.reply_text(msg)
    except Exception as e:
        log.warning("Impossibile notificare l'utente dell'errore: %s", e)


async def _post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("prenota", "Nuova prenotazione (wizard)"),
        BotCommand("domattina", "Prenota domattina al Piano 1"),
        BotCommand("slot", "Disponibilità prossimi giorni"),
        BotCommand("profilo", "Vedi il tuo profilo"),
        BotCommand("registra", "Registra il tuo profilo"),
        BotCommand("cancella_profilo", "Elimina i tuoi dati dal bot"),
        BotCommand("start", "Schermata iniziale"),
    ])
    log.info("Comandi bot registrati. Bot avviato.")


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("TELEGRAM_BOT_TOKEN non impostato nel .env")
        return 1

    admins_raw = (
        os.environ.get("TELEGRAM_ADMIN_CHAT_IDS")
        or os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    )
    admins = parse_chat_ids(admins_raw)
    if not admins:
        log.error("TELEGRAM_ADMIN_CHAT_IDS vuoto o non valido")
        return 1

    users.init_db()
    bootstrap_admin_from_env(admins)

    session = build_session()

    app = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .build()
    )
    app.bot_data["admins"] = admins
    app.bot_data["session"] = session

    # Comandi base
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("prenota", cmd_prenota))
    app.add_handler(CommandHandler("domattina", cmd_domattina))
    app.add_handler(CommandHandler("slot", cmd_slot))
    app.add_handler(CommandHandler("profilo", cmd_profilo))
    app.add_handler(CommandHandler("cancella_profilo", cmd_cancella_profilo))
    app.add_handler(CommandHandler("admin_utenti", cmd_admin_utenti))

    # ConversationHandler /registra
    conv = ConversationHandler(
        entry_points=[CommandHandler("registra", reg_start)],
        states={
            REG_CF: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_cf)],
            REG_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_email)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone)],
            REG_NOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_nome)],
            REG_CONFIRM: [CallbackQueryHandler(on_callback, pattern=r"^reg:")],
        },
        fallbacks=[CommandHandler("annulla", reg_cancel)],
        name="registra",
        persistent=False,
    )
    app.add_handler(conv)

    # Tutti gli altri callback (wizard, admin, profilo, home)
    app.add_handler(CallbackQueryHandler(on_callback))

    # Error handler globale
    app.add_error_handler(on_error)

    log.info("Admin chat_ids: %s", admins)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())

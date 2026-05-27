"""Storage SQLite + validazioni per i profili utente del bot.

Schema:
    users(
        chat_id INTEGER PRIMARY KEY,
        codice_fiscale TEXT NOT NULL,
        email TEXT NOT NULL,
        telefono TEXT NOT NULL,
        cognome_nome TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',     -- pending|approved|banned
        telegram_username TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        approved_at TEXT
    )

I dati personali (CF, email, telefono) sono salvati in chiaro. Il file vive
nel volume Docker `data/users.db`; chi ha accesso al server li può leggere.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "users.db"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_BANNED = "banned"

_CF_RE = re.compile(r"^[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class User:
    chat_id: int
    codice_fiscale: str
    email: str
    telefono: str
    cognome_nome: str
    status: str
    telegram_username: str | None
    created_at: str
    approved_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(**dict(row))

    def is_approved(self) -> bool:
        return self.status == STATUS_APPROVED


# ---------- DB lifecycle ----------

def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                codice_fiscale TEXT NOT NULL,
                email TEXT NOT NULL,
                telefono TEXT NOT NULL,
                cognome_nome TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                telegram_username TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                approved_at TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS prenotazioni (
                codice TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL,
                giorno TEXT NOT NULL,           -- YYYY-MM-DD
                fascia TEXT NOT NULL,           -- HH:MM-HH:MM
                area_id INTEGER NOT NULL,
                postazione TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                cancelled_at TEXT
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_prenotazioni_chat ON prenotazioni(chat_id)"
        )


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ---------- CRUD ----------

def get_user(chat_id: int) -> User | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
    return User.from_row(row) if row else None


def is_approved(chat_id: int) -> bool:
    u = get_user(chat_id)
    return bool(u and u.is_approved())


def upsert_pending(
    chat_id: int,
    codice_fiscale: str,
    email: str,
    telefono: str,
    cognome_nome: str,
    telegram_username: str | None,
) -> None:
    """Inserisce o aggiorna un profilo in stato pending.

    Se esiste già un profilo approved per quel chat_id, lo riporta a pending
    (caso: utente vuole modificare i propri dati e li ri-sottomette).
    """
    with _conn() as c:
        c.execute(
            """
            INSERT INTO users (chat_id, codice_fiscale, email, telefono, cognome_nome,
                               status, telegram_username, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                codice_fiscale=excluded.codice_fiscale,
                email=excluded.email,
                telefono=excluded.telefono,
                cognome_nome=excluded.cognome_nome,
                status='pending',
                telegram_username=excluded.telegram_username,
                approved_at=NULL
            """,
            (chat_id, codice_fiscale, email, telefono, cognome_nome, telegram_username),
        )


def upsert_approved(
    chat_id: int,
    codice_fiscale: str,
    email: str,
    telefono: str,
    cognome_nome: str,
    telegram_username: str | None = None,
) -> None:
    """Inserisce un profilo direttamente come approved (usato per il bootstrap admin)."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO users (chat_id, codice_fiscale, email, telefono, cognome_nome,
                               status, telegram_username, approved_at)
            VALUES (?, ?, ?, ?, ?, 'approved', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO NOTHING
            """,
            (chat_id, codice_fiscale, email, telefono, cognome_nome, telegram_username),
        )


def approve(chat_id: int) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE users SET status='approved', approved_at=CURRENT_TIMESTAMP WHERE chat_id = ?",
            (chat_id,),
        )
    return cur.rowcount > 0


def ban(chat_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE users SET status='banned' WHERE chat_id = ?", (chat_id,))
    return cur.rowcount > 0


def delete(chat_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
    return cur.rowcount > 0


def list_all() -> list[User]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [User.from_row(r) for r in rows]


# ---------- prenotazioni ----------

@dataclass
class Prenotazione:
    codice: str
    chat_id: int
    entry_id: int
    giorno: str
    fascia: str
    area_id: int
    postazione: str | None
    created_at: str
    cancelled_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Prenotazione":
        return cls(**dict(row))


def add_prenotazione(
    codice: str,
    chat_id: int,
    entry_id: int,
    giorno: str,
    fascia: str,
    area_id: int,
    postazione: str | None,
) -> None:
    with _conn() as c:
        c.execute(
            """
            INSERT INTO prenotazioni
              (codice, chat_id, entry_id, giorno, fascia, area_id, postazione)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(codice) DO NOTHING
            """,
            (codice, chat_id, entry_id, giorno, fascia, area_id, postazione),
        )


def list_prenotazioni_attive(chat_id: int, today_iso: str) -> list[Prenotazione]:
    """Prenotazioni dell'utente non cancellate e da oggi in poi."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM prenotazioni
            WHERE chat_id = ? AND cancelled_at IS NULL AND giorno >= ?
            ORDER BY giorno ASC, fascia ASC
            """,
            (chat_id, today_iso),
        ).fetchall()
    return [Prenotazione.from_row(r) for r in rows]


def get_prenotazione(codice: str) -> Prenotazione | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM prenotazioni WHERE codice = ?", (codice,)
        ).fetchone()
    return Prenotazione.from_row(row) if row else None


def mark_cancelled(codice: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE prenotazioni SET cancelled_at = CURRENT_TIMESTAMP WHERE codice = ? AND cancelled_at IS NULL",
            (codice,),
        )
    return cur.rowcount > 0


# ---------- payload utenti ----------

def booking_payload(chat_id: int) -> tuple[dict, str] | None:
    """Estrae (utente_dict, cognome_nome) come servono a book.prenota_e_conferma."""
    u = get_user(chat_id)
    if not u or not u.is_approved():
        return None
    return (
        {"codice_fiscale": u.codice_fiscale, "email": u.email, "phone": u.telefono},
        u.cognome_nome,
    )


# ---------- validazioni ----------

def validate_cf(s: str) -> str:
    """Ritorna il CF normalizzato. Solleva ValueError se invalido."""
    normalized = s.strip().upper().replace(" ", "")
    if not _CF_RE.match(normalized):
        raise ValueError(
            "Codice fiscale non valido. Formato atteso: 16 caratteri, "
            "lettere e numeri (es. RSSMRA80A01H501U)."
        )
    return normalized


def validate_email(s: str) -> str:
    normalized = s.strip()
    if not _EMAIL_RE.match(normalized) or len(normalized) > 254:
        raise ValueError("Email non valida.")
    return normalized


def validate_phone(s: str) -> str:
    """Lascia spazi/+ per leggibilità; verifica solo la sostanza."""
    digits = re.sub(r"\D", "", s)
    if not (8 <= len(digits) <= 13):
        raise ValueError("Telefono non valido (servono 8-13 cifre).")
    return s.strip()


def validate_nome(s: str) -> str:
    normalized = s.strip()
    if not (3 <= len(normalized) <= 80):
        raise ValueError("Nome troppo corto o troppo lungo (3-80 caratteri).")
    if not re.search(r"\s", normalized):
        raise ValueError("Inserisci sia il cognome sia il nome (separati da spazio).")
    return normalized

# AutomaticMozzoLibrary

Bot Telegram (con CLI Python di supporto) per automatizzare la prenotazione di
un posto studio alla **Biblioteca di Mozzo (BG)** sul portale easystaff.

Sostituisce la procedura web `Nuova prenotazione → seleziona servizio → durata
→ sede → giorno → fascia → compila modulo` con un'unica chat Telegram a
bottoni: in 3 tap hai prenotato. Supporta più utenti, con onboarding e
approvazione dall'admin.

---

## Cosa fa

- **Prenotazione a bottoni inline**: `Sede → Giorno → Fascia → Conferma`,
  niente da digitare. Lo schermo si aggiorna ad ogni passo (non spamma
  messaggi nuovi).
- **Scorciatoia 1-tap** "Domattina, Piano 1" per il caso d'uso più frequente.
- **Disponibilità in tempo reale**: prima di mostrarti un giorno o una fascia,
  il bot chiede al portale quanti posti sono ancora liberi e mostra
  contatori tipo `M 12/18  P 18/18` (mattina/pomeriggio).
- **Annullamento prenotazione** direttamente dal bot, con riepilogo e
  conferma.
- **Multi-utente con approvazione**: ogni nuovo utente fa `/registra`,
  l'admin riceve una notifica push con `[Approva][Rifiuta]`. I limiti del
  portale (14 prenotazioni / 7gg per codice fiscale) sono indipendenti tra
  utenti.
- **GDPR-friendly**: l'utente può vedere i propri dati con `/profilo` e
  cancellarli con `/cancella_profilo`.
- **CLI Python autonoma** (`book.py`) per chi vuole usare gli stessi
  endpoint da terminale o da script: `book.py aree`, `book.py slot`,
  `book.py prenota`.
- **Deploy in container**: `docker compose up -d` e basta. Restart
  automatico, stato persistente su `data/users.db`.

## Per chi è

Pensato per chi prenota regolarmente un posto studio alla Biblioteca di
Mozzo, e per i suoi amici/conoscenti che vogliono accesso allo stesso bot
senza dover passare ogni volta dal portale.

Il portale easystaff è in uso da molte biblioteche e enti italiani: con
piccoli aggiustamenti delle costanti in `book.py` (slug del cliente, ID
servizio, ID aree) il bot può essere adattato ad altre installazioni della
stessa piattaforma.

---

## Quick start (Docker)

```bash
git clone https://github.com/MCGabbo/AutomaticMozzoLibrary.git
cd AutomaticMozzoLibrary

# 1. Configura
cp .env.example .env
nano .env                   # vedi sezione "Configurazione" sotto

# 2. Prepara la cartella dati e i permessi (richiesto solo la prima volta)
mkdir -p data
sudo chown -R 1000:1000 data

# 3. Avvia
docker compose up -d --build
docker compose logs -f
```

I log devono mostrare:
```
INFO: Admin <chat_id> bootstrappato nel DB da .env
INFO: Admin chat_ids: {<chat_id>}
INFO: Comandi bot registrati. Bot avviato.
INFO: Application started
```

Apri Telegram, cerca il tuo bot, manda `/start`.

### Aggiornamenti

```bash
git pull
docker compose up -d --build
```

### Senza Docker

```bash
python3 -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -r requirements.txt
python bot.py
```

---

## Configurazione (`.env`)

1. **Crea il bot Telegram**
   - Apri Telegram, cerca `@BotFather`, invia `/newbot`.
   - Scegli nome (es. `AutoBiblio Mozzo`) e username (deve finire in `bot`).
   - BotFather ti darà un token `1234567890:AA...`.

2. **Trova il tuo chat_id (admin)**
   - Cerca `@userinfobot` su Telegram, premi Start.
   - Annota il numero `Id: 123456789`.

3. **Compila `.env`**

```env
# --- Dati personali admin (usati al primo avvio per popolare il DB) ---
CODICE_FISCALE=RSSMRA80A01H501U
EMAIL=mario.rossi@example.com
TELEFONO=333 1234567
COGNOME_NOME=Rossi Mario

# --- Telegram bot ---
TELEGRAM_BOT_TOKEN=1234567890:AA...
TELEGRAM_ADMIN_CHAT_IDS=123456789
```

`TELEGRAM_ADMIN_CHAT_IDS` accetta più id separati da virgola (utile se più
admin). I dati personali nel `.env` servono **solo** al primo avvio per
creare automaticamente il tuo profilo come `approved` nel database; dopo
puoi anche rimuoverli, il bot vive solo del DB.

> Il file `.env` è in `.gitignore` e `.dockerignore`: i tuoi segreti non
> finiscono mai in repository né nelle immagini Docker.

---

## Comandi del bot

### Utente

| Comando | Cosa fa |
|---|---|
| `/start` | Schermata iniziale con i 4 bottoni principali |
| `/registra` | Wizard di registrazione (CF, email, telefono, cognome e nome) |
| `/profilo` | Mostra i propri dati e lo stato (in attesa / approvato) |
| `/cancella_profilo` | Elimina i propri dati dal bot |
| `/prenota` | Wizard prenotazione |
| `/domattina` | Scorciatoia: domattina al Piano 1 |
| `/annulla` | Annulla una prenotazione esistente |
| `/slot` | Disponibilità prossimi 7 giorni su entrambe le sedi |

### Admin

| Comando | Cosa fa |
|---|---|
| `/admin_utenti` | Lista degli utenti registrati con stato e chat_id |
| (notifica push) | Bottoni `[Approva][Rifiuta]` arrivano automaticamente quando un nuovo utente completa `/registra` |

### Schermata home

```
  ⚡ Domattina, Piano 1
  📅 Nuova prenotazione
  🔍 Slot disponibili
  🗑️ Annulla prenotazione
```

---

## Comandi della CLI

`book.py` espone direttamente le stesse funzioni del bot, con dati letti
dal `.env` (single-user).

```bash
python book.py aree                                       # lista aree
python book.py slot --giorno domani --sede piano1         # disponibilità
python book.py prenota --giorno domani --fascia mattina --sede piano1
python book.py prenota --giorno 2026-05-30 --fascia 14:30 --sede narrativa --dry-run
```

Alias supportati:
- **giorno**: `oggi`, `domani`, `dopodomani`, `lunedi`..`domenica`
  (o `lun`..`dom`), `YYYY-MM-DD`
- **fascia**: `mattina` (=09:30), `pomeriggio` (=14:30), oppure `HH:MM`
- **sede**: `piano1` (=67), `narrativa` (=71), `singole` (=77), oppure ID

Durata prenotazione: sempre 3 ore (limite del portale).

---

## Come funziona

Il bot non simula un browser: il portale easystaff espone un'**API REST
JSON pulita** (`/portalePlanningNewAPI/api/...`), senza autenticazione,
senza CSRF, senza captcha effettivo. Il bot usa `requests` per chiamare
direttamente gli endpoint scoperti via reverse engineering dei file HAR
del traffico di rete.

Endpoint principali:

```
GET  /api/aree/9                                                 # sedi
GET  /api/entry/130/schedule/{YYYY-MM-DD}/{area}/10800           # slot giorno
POST /api/entry/store                                            # crea
POST /api/entry/confirm/{entry_id}                               # conferma
POST /api/entry/delete/{codice}?chiave={cf}                      # annulla
```

Stack:
- **Python 3.12** (3.10+ supportato)
- **`requests`** + `urllib3.Retry` per resilienza alle connessioni morte
- **`python-telegram-bot` v21** con `Application`, `CommandHandler`,
  `ConversationHandler`, `CallbackQueryHandler`
- **SQLite** (`stdlib`) per profili utenti e storico prenotazioni
- **`asyncio.gather`** per parallelizzare le GET di disponibilità
  (~5s → <1s)
- **Docker** per il deploy, con bind mount `./data:/app/data` per la
  persistenza del DB

---

## Vincoli del portale

Imposti server-side e non aggirabili dal client:

- Massimo **7 giorni** di anticipo per ogni prenotazione.
- Massimo **14 prenotazioni** ogni 7 giorni (rolling) per codice fiscale.
- Ritardi >30' o assenze non comunicate per 2 volte in 15 giorni → ban di
  15 giorni. Se non puoi venire, **annulla** la prenotazione (`/annulla`).

---

## Limiti noti

- L'`/annulla` mostra solo prenotazioni **create attraverso il bot**.
  Quelle fatte direttamente dal portale web vanno cancellate dal portale.
- Lo stato del wizard di prenotazione vive in memoria (`context.user_data`):
  se il container riparte a metà flusso, basta rifare `/prenota`.
- Solo le due fasce standard di 3h (`09:30-12:30`, `14:30-17:30`) sono
  esposte nei bottoni del bot. La CLI accetta orari arbitrari (es.
  `--fascia 15:00`).
- Le costanti di sede/servizio sono cablate sulla biblioteca di Mozzo;
  riadattamento richiede modifiche a `book.py` (variabili `CLIENTE_*`,
  `ENTRY_TYPE`, `SEDE_ALIAS`).

---

## Privacy

Il bot conserva, per ogni utente registrato:
codice fiscale, email, telefono, cognome+nome, chat_id Telegram, eventuale
username Telegram, timestamp di registrazione e approvazione, e l'elenco
delle prenotazioni create.

Tutti questi dati stanno in `data/users.db` (SQLite) **in chiaro**. Il file
non viene mai pushato (`.gitignore`/`.dockerignore`). Per il deploy server:
proteggi la cartella `data/` a livello filesystem e includila nei backup.
Ogni utente può autodistruggere il proprio profilo con `/cancella_profilo`.

---

## Deploy alternativo: systemd (senza Docker)

```ini
# /etc/systemd/system/autobiblio-bot.service
[Unit]
Description=AutomaticMozzoLibrary Telegram bot
After=network-online.target

[Service]
Type=simple
User=tuo-utente
WorkingDirectory=/home/tuo-utente/AutomaticMozzoLibrary
ExecStart=/home/tuo-utente/AutomaticMozzoLibrary/.venv/bin/python bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autobiblio-bot
journalctl -u autobiblio-bot -f
```

---

## Sviluppo

Il progetto è 3 file Python + Docker setup:

- `book.py` — CLI argparse + core API (`build_session`, `lista_aree`,
  `slot_giorno`, `prenota_e_conferma`, `cancella_prenotazione`)
- `users.py` — SQLite (tabelle `users`, `prenotazioni`) + validazioni
  (CF italiano, email, telefono, nome)
- `bot.py` — bot Telegram, importa il core da `book.py` e lo storage da
  `users.py`

Per testare in locale senza Docker basta `python bot.py` con un `.env`
compilato. Solo un'istanza alla volta può fare polling su Telegram con
lo stesso token: se sviluppi in locale, prima ferma il container sul server.

---

## Licenza

Distribuito sotto licenza [GPL-3.0](LICENSE).

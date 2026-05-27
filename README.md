# AutomaticMozzoLibrary

CLI Python per automatizzare la prenotazione di un posto studio sul portale
[easystaff della Biblioteca di Mozzo](https://easyplanning.easystaff.it/portalePlanningNew/mozzo-biblio).

Il portale espone API REST JSON pulite: lo script le chiama direttamente,
senza browser headless. Tre comandi: `aree`, `slot`, `prenota`.

## Setup

```bash
git clone https://github.com/MCGabbo/AutomaticMozzoLibrary.git
cd AutomaticMozzoLibrary
python3 -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -r requirements.txt
```

Copia `.env.example` in `.env` e compila con i tuoi dati personali:

```env
CODICE_FISCALE=RSSMRA80A01H501U
EMAIL=mario.rossi@example.com
TELEFONO=333 1234567
COGNOME_NOME=Rossi Mario
```

`.env` è già in `.gitignore`, non finirà mai sul repository.

## Uso

### Lista sedi disponibili

```bash
python book.py aree
```

Stampa l'elenco delle aree con `ID  NOME  CODE`. Da qui si scoprono nuovi
ID se la biblioteca aggiunge zone.

### Verifica slot disponibili in un giorno

```bash
python book.py slot --giorno domani --sede piano1
python book.py slot --giorno 2026-05-30 --sede narrativa
```

Output: ogni fascia da 3h con `[OK]` o `[--]` e il numero di postazioni libere.

### Prenotare

```bash
python book.py prenota --giorno domani --fascia mattina --sede piano1
python book.py prenota --giorno venerdi --fascia pomeriggio --sede narrativa
python book.py prenota --giorno 2026-05-29 --fascia 15:00 --sede 67 --dry-run
```

Restituisce codice prenotazione e numero di postazione assegnata. Con
`--dry-run` verifica disponibilità e prepara il payload ma **non** conferma.

## Parametri

| Flag | Valori accettati |
|---|---|
| `--giorno` | `oggi`, `domani`, `dopodomani`, `lunedi`..`domenica` (anche `lun`..`dom`), `YYYY-MM-DD` |
| `--fascia` | `mattina` (=09:30), `pomeriggio` (=14:30), oppure `HH:MM` arbitrario |
| `--sede` | `piano1` (=67), `narrativa` (=71), `singole` (=77), oppure ID numerico |

Durata prenotazione: fissa 3 ore (10800 secondi), come da prassi della biblioteca.

## Vincoli della biblioteca

Imposti dal portale, replicati dallo script solo via messaggio d'errore:

- Massimo **7 giorni** di anticipo sulla prenotazione.
- Massimo **14 prenotazioni** in 7 giorni rolling.
- Ritardi >30' o assenze non comunicate per 2 volte in 15 giorni → ban di 15
  giorni. Cancella la prenotazione se non puoi andare:
  [Gestisci prenotazione](https://easyplanning.easystaff.it/portale/mozzo-biblio/index.php?include=manage).

## Bot Telegram

`bot.py` espone le stesse funzioni della CLI via bot Telegram con interfaccia
a bottoni inline (nessun comando da digitare a mano oltre a `/start`).

### Setup bot

1. Su Telegram cerca `@BotFather`, manda `/newbot`, scegli nome+username, salva
   il token.
2. Cerca `@userinfobot` e annota il tuo `Id` numerico.
3. Aggiungi al tuo `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=1234567890:AA...
   TELEGRAM_ALLOWED_CHAT_IDS=123456789
   ```
   `TELEGRAM_ALLOWED_CHAT_IDS` accetta più id separati da virgola; tutti gli
   altri chat_id vengono rifiutati.
4. Installa la dipendenza extra:
   ```bash
   pip install -r requirements.txt
   ```
5. Avvia il bot:
   ```bash
   python bot.py
   ```

### Comandi

| Comando | Cosa fa |
|---|---|
| `/start` | Schermata iniziale con scorciatoie |
| `/prenota` | Wizard a bottoni: sede → giorno → fascia → conferma |
| `/domattina` | Prenota subito mattina al Piano 1 di domani (un tap) |
| `/slot` | Mostra disponibilità prossimi 7 giorni su entrambe le sedi |

I bottoni del wizard mostrano solo i giorni e le fasce con posti effettivamente
liberi. Lo stato del wizard sta in memoria del processo: se il bot riparte,
basta digitare `/prenota` per ricominciare.

### Deploy su server (systemd)

Esempio di unit `/etc/systemd/system/autobiblio-bot.service`:

```ini
[Unit]
Description=AutomaticMozzoLibrary Telegram bot
After=network-online.target

[Service]
Type=simple
User=gabri
WorkingDirectory=/home/gabri/AutomaticMozzoLibrary
ExecStart=/home/gabri/AutomaticMozzoLibrary/.venv/bin/python bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Poi:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autobiblio-bot
journalctl -u autobiblio-bot -f
```

## Reverse engineering

Le API usate sono state estratte ispezionando il traffico di rete del portale
con DevTools (filtro Network → XHR/Fetch) durante una prenotazione manuale.
Gli endpoint chiave:

```
GET  /api/aree/9
GET  /api/servizi/9
GET  /api/entry/130/schedule/{YYYY-MM-DD}/{area_id}/10800
POST /api/entry/store
POST /api/entry/confirm/{entry_id}
```

Nessuna autenticazione, nessun CSRF, nessun reCAPTCHA effettivo
(`recaptchaToken: null` viene accettato). Se in futuro il portale cambia
API serve rifare la ricognizione.

## Licenza

[GPL-3.0](LICENSE)

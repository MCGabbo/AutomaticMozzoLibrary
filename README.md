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

## Bot Telegram (multi-utente)

`bot.py` espone le funzioni della CLI come bot Telegram con interfaccia a
bottoni inline. Supporta più utenti: ognuno fornisce i propri dati al primo
accesso e — dopo l'approvazione dell'admin — può prenotare a proprio nome.
I limiti del portale (14 prenotazioni/7gg) sono per codice fiscale, quindi ogni
utente ha il suo budget indipendente.

### Setup bot

1. Su Telegram cerca `@BotFather`, manda `/newbot`, scegli nome+username, salva
   il token.
2. Per l'admin: cerca `@userinfobot` e annota il tuo `Id` numerico.
3. Aggiungi al `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=1234567890:AA...
   TELEGRAM_ADMIN_CHAT_IDS=123456789
   # Dati admin (opzionali ma consigliati): vengono usati al primo avvio per
   # creare il tuo profilo già approvato nel DB.
   CODICE_FISCALE=RSSMRA80A01H501U
   EMAIL=mario.rossi@example.com
   TELEFONO=333 1234567
   COGNOME_NOME=Rossi Mario
   ```
4. Installa le dipendenze e avvia:
   ```bash
   pip install -r requirements.txt
   python bot.py
   ```

I profili degli utenti vengono salvati in `data/users.db` (SQLite). Il file
viene creato automaticamente al primo avvio.

### Comandi utente

| Comando | Cosa fa |
|---|---|
| `/start` | Schermata iniziale |
| `/registra` | Wizard per fornire i propri dati (CF, email, telefono, nome) |
| `/profilo` | Mostra il proprio profilo e lo stato (in attesa / approvato) |
| `/cancella_profilo` | Elimina i propri dati dal bot |
| `/prenota` | Wizard prenotazione: sede → giorno → fascia → conferma |
| `/domattina` | Scorciatoia: mattina al Piano 1 di domani |
| `/slot` | Disponibilità prossimi 7 giorni su entrambe le sedi |

### Comandi admin

| Comando | Cosa fa |
|---|---|
| `/admin_utenti` | Lista degli utenti registrati con stato e chat_id |

L'admin riceve **una notifica Telegram con bottoni `[Approva][Rifiuta]`** ogni
volta che un nuovo utente completa la registrazione.

### Flusso onboarding

1. Un nuovo utente apre la chat col bot e fa `/start`.
2. Bot lo invita a fare `/registra`.
3. Bot chiede in sequenza: codice fiscale → email → telefono → cognome e nome.
4. Riepilogo e conferma. La registrazione passa in stato `pending`.
5. L'admin riceve la notifica con i dati e i bottoni `[Approva][Rifiuta]`.
6. Approvazione → l'utente riceve un messaggio di conferma e può prenotare.

### Privacy

Il file `data/users.db` contiene dati personali (codici fiscali) in chiaro.
È nel `.gitignore` e nel `.dockerignore`, quindi non finisce nei push. Per il
deploy server: assicurati che la cartella `data/` sia protetta nei permessi
filesystem e backuppata in modo sicuro se contiene dati di terzi.

### Deploy su server con Docker (consigliato)

Il repo include `Dockerfile` e `docker-compose.yml`. Il `docker-compose.yml`
mappa la cartella `./data` come volume bind: lì vive `users.db` con i
profili degli utenti registrati. Lo stato volatile del wizard di
prenotazione resta in memoria del processo: se il container riparte basta
rifare `/prenota`, i profili e le approvazioni invece sopravvivono.

```bash
git clone https://github.com/MCGabbo/AutomaticMozzoLibrary.git
cd AutomaticMozzoLibrary
cp .env.example .env
nano .env                   # compila CF, email, telefono, nome, token, admin chat_id
mkdir -p data               # cartella per users.db (l'host la crea col tuo user)
docker compose up -d --build
docker compose logs -f
```

> Se vedi errori di permessi su `data/users.db`, allinea il proprietario al
> non-root user del container: `sudo chown -R 1000:1000 data`.

Aggiornamento:
```bash
git pull
docker compose up -d --build
```

Stop / riavvio:
```bash
docker compose restart
docker compose down
```

Il `.env` viene letto a runtime tramite `env_file`, non finisce mai dentro
l'immagine: l'immagine può essere ricostruita liberamente senza esporre
segreti.

### Deploy su server con systemd (alternativa senza Docker)

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

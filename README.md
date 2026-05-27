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

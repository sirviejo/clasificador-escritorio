# Jev Tidy my Desktop

[English](README.md) · [Español](README.es.md) · [Français](README.fr.md) · **Italiano**

Riordina la Scrivania di macOS con [TypeSafe](https://docs.typesafe.ai). Per ogni file sciolto decide:

| Decisione | Chi la prende |
| --- | --- |
| **Le schermate vanno sempre nella cartella delle schermate** (`Schermate/`, o quella che scegli) | Il codice (nome del file o metadato di macOS che sopravvive alle rinomine) |
| **Cartella** di destinazione | TypeSafe (`Choice`); confidenza bassa → `Da_Rivedere/` |
| **Sottocartella** dentro quella cartella | TypeSafe (`Choice`) |
| **Importanza**, da eliminabile a «non si può perdere» | TypeSafe (`Score` 0–3) |
| Contiene **dati sensibili**? È **sostituibile**? È **temporaneo**? | TypeSafe (`Noul`, una probabilità ciascuna) |
| È un **duplicato esatto**? | Il codice (SHA-256) |
| **Verdetto**: conservare / archiviare / eliminabile | Il codice, combinando tutto quanto sopra con soglie modificabili |

**Non elimina mai nulla.** I candidati all’eliminazione vengono messi da parte in `Da_Eliminare/` perché tu li
controlli, ogni esecuzione si può annullare con `--undo`, e un report CSV conserva tutti i punteggi.

I nomi delle cartelle e i messaggi sono disponibili in **inglese, spagnolo, francese e italiano**.

## Requisiti

- macOS (usa `xattr`, `mdls`, `textutil` e, per leggere immagini e PDF, Vision e PDFKit)
- Python 3.9+ — nessuna dipendenza, basta il `python3` di sistema
- Una API key di TypeSafe: https://console.typesafe.ai/
- Facoltativo: Xcode Command Line Tools (`swiftc`) per l’OCR di `--with-content`

## Installazione

Lo script riordina **la cartella che contiene la propria cartella**, quindi clonalo dentro la Scrivania:

```bash
cd ~/Desktop
git clone https://github.com/sirviejo/jev-tidy-my-desktop.git
cd jev-tidy-my-desktop
cp .env.example .env    # poi compila TYPESAFE_API_KEY
```

## Uso

```bash
python3 clasificar.py --dry               # mostra cosa succederebbe, senza spostare nulla
python3 clasificar.py                     # classifica e sposta
python3 clasificar.py --with-content      # legge anche ogni file (vedi «Privacy»)
python3 clasificar.py --folder Schermate  # analizza i file sciolti di una cartella della Scrivania
python3 clasificar.py --limit 20          # solo i primi 20 file
python3 clasificar.py --screenshots-only  # sposta solo le schermate; non chiama mai l’API
python3 clasificar.py --screenshots-dir "~/Pictures/Schermate"   # un’altra cartella per le schermate
python3 clasificar.py --lang it           # lingua di cartelle e messaggi
python3 clasificar.py --undo              # annulla l’ultima esecuzione
```

Esempio di output:

```
Analisi di 3 file…
  Finanze_Fatturazione/Fatture_Emesse/ ← INVOICE_0042_ACME.pdf  (conservare: importante + dati sensibili · importanza 3.0/3)
  Schermate/Mappe_Luoghi/              ← Schermata 2026-04-22 alle 20.30.18.png  (archiviare: nessun motivo per eliminare né per segnalare · importanza 0.8/3)
  Da_Eliminare/                        ← Zoom-installer.dmg  (eliminare: sostituibile · importanza 0.1/3)

Importanti (conservare): 1 · Archiviati: 1 · Eliminabili: 1 (messi da parte in Da_Eliminare/, nulla viene eliminato automaticamente)
Report: informes/informe-20260919-125152.csv
```

## Lingue

I nomi di cartelle, sottocartelle e i messaggi vengono da `locales/<lingua>.json`. Sono inclusi **en, es, fr, it**.
Scegli con `--lang`, con `LANGUAGE` nel `.env`, oppure lascia che segua la lingua di sistema (con ripiego
sull’inglese se manca la traduzione).

Tutto ciò che legge il modello (domande, descrizioni delle categorie, id delle opzioni) è sempre in inglese,
quindi la lingua scelta **non cambia i giudizi**, solo il nome delle cartelle. Le schermate vengono
riconosciute dal nome nelle quattro lingue e, in qualsiasi altra, dal metadato di macOS.

Per aggiungere una lingua: copia `locales/en.json` in `locales/<codice>.json`, traduci i valori (non le
chiavi) e mantieni i `%s` / `%d` dei messaggi.

## Come funziona

Il codice governa il flusso; il modello risponde solo a domande piccole e tipizzate.

1. **Prima le regole fisse.** Schermate e duplicati esatti vengono rilevati senza IA.
2. **Una richiesta per lotto di file.** Tutte le domande su tutti i file del lotto viaggiano insieme e ricevono
   risposta in parallelo. Le domande sulla sottocartella sono *speculative*: se ne pone una per ogni cartella
   possibile («supponendo che vada in X, in quale sottocartella?») e il codice legge solo quella della
   cartella scelta, invece di fare una seconda richiesta.
3. **La policy vive nel codice.** Il verdetto combina i punteggi con soglie poste all’inizio di
   `clasificar.py` (`MIN_CONFIDENCE`, `IMPORTANT_FROM`, `DELETABLE_MIN_SIGNAL`…). Cambiarle non richiede di
   richiamare l’API: i punteggi grezzi sono nel CSV.

| Verdetto | Regola |
| --- | --- |
| `keep` / conservare | importanza ≥ 2.0 **oppure** probabilità di dati sensibili ≥ 0.7 |
| `delete` / eliminare → `Da_Eliminare/` | duplicato esatto, **oppure** importanza ≤ 1.0 e (sostituibile o temporaneo) ≥ 0.6 |
| `file` / archiviare | tutto il resto: va nella sua cartella senza alcun contrassegno |

Le cartelle già presenti sulla Scrivania vengono proposte automaticamente come opzioni (descritte da ciò che
contengono), così come le sottocartelle esistenti. Le categorie e sottocartelle predefinite si trovano in
`CATEGORIES` e `SUBFOLDERS` (descrizioni in inglese, per il modello) e i loro nomi in `locales/`: adattale al
tuo modo di lavorare.

## Configurazione (`.env`)

| Variabile | |
| --- | --- |
| `TYPESAFE_API_KEY` | Obbligatoria. |
| `OWNER` | Facoltativa. Chi sei (`La Mia Azienda / Il Mio Nome`), per distinguere fatture emesse e ricevute. |
| `LANGUAGE` | Facoltativa. `en`, `es`, `fr` o `it`. Per impostazione predefinita, la lingua di sistema. |
| `SCREENSHOTS_DIR` | Facoltativa. Cartella delle schermate: un nome dentro la Scrivania o qualsiasi percorso (`~/Pictures/Schermate`). `--screenshots-dir` ha la precedenza. |
| `REVIEW_DIR`, `TO_DELETE_DIR` | Facoltative. Rinominano le cartelle di revisione e dei candidati all’eliminazione. |
| `IGNORE` | Facoltativa. File o cartelle della Scrivania, separati da virgola, da non toccare né proporre come destinazione. |

## Privacy

- **Per impostazione predefinita** a TypeSafe vengono inviati solo metadati: nome, estensione, tipo,
  dimensione, data e URL di download. Le schermate non vengono analizzate (il nome non dice nulla): vanno
  direttamente nella loro cartella.
- **Con `--with-content`** vengono inviati anche i primi 1.200 caratteri di ogni file: testo semplice,
  documenti (`textutil`), PDF e l’**OCR delle immagini**, eseguito in locale con Vision. Una schermata del tuo
  home banking include il saldo: usalo sapendolo.
- `.env`, `informes/` e `movimientos.jsonl` contengono la tua key e i nomi dei tuoi file; sono nel `.gitignore`.

Costo: Jev fa pagare i token in ingresso (0,042 USD per milione al momento della stesura); qualche centinaio
di file costa pochi centesimi.

## Spostare automaticamente le nuove schermate (facoltativo)

`com.clasificador.capturas.plist` è un LaunchAgent che esegue `--screenshots-only` (senza API) ogni volta che la Scrivania cambia:

```bash
sed -e "s#__DIR__#$PWD#g" -e "s#__DESKTOP__#$(dirname "$PWD")#g" com.clasificador.capturas.plist > ~/Library/LaunchAgents/com.clasificador.capturas.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.clasificador.capturas.plist
```

La prima volta macOS può chiedere il permesso perché `python3` acceda alla Scrivania. Per disinstallarlo:
`launchctl bootout gui/$(id -u)/com.clasificador.capturas` ed elimina il `.plist`.

Alternativa senza script: `defaults write com.apple.screencapture location ~/Desktop/Schermate`.

## Limiti

- Solo macOS.
- Elabora i file sciolti di una cartella; non sposta cartelle né entra nelle sottocartelle.
- Le soglie sono un punto di partenza ragionevole, non una verità: esegui con `--dry`, guarda il CSV e regolale.
- Il testo di un file è contenuto non attendibile: un file potrebbe essere scritto per confondere il modello.
  Nel peggiore dei casi finisce in un’altra cartella.
- Le risposte tipizzate garantiscono il formato, non che il giudizio sia corretto. Per questo nulla viene
  eliminato automaticamente.

## Licenza

[MIT](LICENSE)

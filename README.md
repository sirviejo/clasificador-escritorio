# Clasificador de Escritorio — Desktop Classifier

**English** · [Español](README.es.md)

Tidies the macOS Desktop with [TypeSafe](https://docs.typesafe.ai). For every loose file it decides:

| Decision | Who makes it |
| --- | --- |
| **Screenshots always go to the screenshots folder** (`Screenshots/`, or the one you choose) | Code (file name or a macOS metadata tag that survives renames) |
| Destination **folder** | TypeSafe (`Choice`); low confidence → `To_Review/` |
| **Subfolder** inside that folder | TypeSafe (`Choice`) |
| **Importance**, from disposable to "cannot be lost" | TypeSafe (`Score` 0–3) |
| Does it hold **sensitive data**? Is it **replaceable**? Is it **temporary**? | TypeSafe (`Noul`, one probability each) |
| Is it an **exact duplicate**? | Code (SHA-256) |
| **Verdict**: keep / file / can be deleted | Code, combining all of the above with editable thresholds |

**It never deletes anything.** Deletion candidates are set aside in `To_Delete/` for you to review, every run
can be reverted with `--undo`, and a CSV report keeps every raw score.

Folder names and messages are available in **English, Spanish, French and Italian**.

## Requirements

- macOS (uses `xattr`, `mdls`, `textutil` and, to read images and PDFs, Vision and PDFKit)
- Python 3.9+ — no dependencies, the system `python3` is enough
- A TypeSafe API key: https://console.typesafe.ai/
- Optional: Xcode Command Line Tools (`swiftc`) for the OCR behind `--with-content`

## Install

The script tidies **the folder that contains its own folder**, so clone it inside the Desktop:

```bash
cd ~/Desktop
git clone https://github.com/sirviejo/clasificador-escritorio.git clasificador
cd clasificador
cp .env.example .env    # then fill in TYPESAFE_API_KEY
```

## Usage

```bash
python3 clasificar.py --dry               # show what would happen, move nothing
python3 clasificar.py                     # classify and move
python3 clasificar.py --with-content      # also read each file (see "Privacy")
python3 clasificar.py --folder Screenshots  # analyze the loose files of a Desktop folder
python3 clasificar.py --limit 20          # only the first 20 files
python3 clasificar.py --screenshots-only  # only move screenshots; never calls the API
python3 clasificar.py --screenshots-dir "~/Pictures/Screenshots"   # another folder for screenshots
python3 clasificar.py --lang fr           # language of folders and messages
python3 clasificar.py --undo              # revert the last run
```

Sample output:

```
Analyzing 3 files…
  Finance_Invoicing/Invoices_Issued/   ← INVOICE_0042_ACME.pdf  (keep: important + sensitive data · importance 3.0/3)
  Screenshots/Maps_Places/             ← Screenshot 2026-04-22 at 8.30.18 PM.png  (file: no reason to delete or to flag · importance 0.8/3)
  To_Delete/                           ← Zoom-installer.dmg  (delete: replaceable · importance 0.1/3)

Important (keep): 1 · Filed: 1 · Can be deleted: 1 (set aside in To_Delete/, nothing is deleted automatically)
Report: informes/informe-20260919-125152.csv
```

## Languages

Folder names, subfolder names and messages come from `locales/<lang>.json`. **en, es, fr, it** are included.
Pick one with `--lang`, with `LANGUAGE` in `.env`, or let it follow the system language (falling back to
English when there is no translation).

Everything the model reads (questions, category descriptions, option ids) is always English, so the chosen
language **does not change the judgments**, only what the folders are called. Screenshots are recognized by
name in all four languages and, in any other, by the macOS metadata tag.

To add a language: copy `locales/en.json` to `locales/<code>.json`, translate the values (not the keys) and
keep the `%s` / `%d` placeholders in the messages.

## How it works

Code owns the workflow; the model only answers small, typed questions.

1. **Fixed rules first.** Screenshots and exact duplicates are detected without AI.
2. **One request per batch of files.** Every question about every file in the batch travels together and is
   answered in parallel. Subfolder questions are *speculative*: one is asked per possible folder ("assuming it
   goes in X, which subfolder?") and the code reads only the one for the folder that was chosen, instead of
   making a second request.
3. **Policy lives in code.** The verdict combines the scores using thresholds at the top of `clasificar.py`
   (`MIN_CONFIDENCE`, `IMPORTANT_FROM`, `DELETABLE_MIN_SIGNAL`…). Changing them does not require calling the
   API again: the raw scores are in the CSV.

| Verdict | Rule |
| --- | --- |
| `keep` | importance ≥ 2.0 **or** probability of sensitive data ≥ 0.7 |
| `delete` → `To_Delete/` | exact duplicate, **or** importance ≤ 1.0 and (replaceable or temporary) ≥ 0.6 |
| `file` | everything else: goes to its folder with no flag |

Folders that already exist on your Desktop are offered as options automatically (described by what they
contain), and so are existing subfolders. The predefined categories and subfolders live in `CATEGORIES` and
`SUBFOLDERS` (English descriptions, for the model) and their names in `locales/`: edit them to match how you work.

## Configuration (`.env`)

| Variable | |
| --- | --- |
| `TYPESAFE_API_KEY` | Required. |
| `OWNER` | Optional. Who you are (`My Company / My Name`), to tell issued invoices from received ones. |
| `LANGUAGE` | Optional. `en`, `es`, `fr` or `it`. Defaults to the system language. |
| `SCREENSHOTS_DIR` | Optional. Screenshots folder: a name inside the Desktop or any path (`~/Pictures/Screenshots`). `--screenshots-dir` takes precedence. |
| `REVIEW_DIR`, `TO_DELETE_DIR` | Optional. Rename the review and deletion-candidates folders. |
| `IGNORE` | Optional. Comma-separated Desktop files or folders to leave alone and never offer as a destination. |

## Privacy

- **By default** only metadata is sent to TypeSafe: name, extension, kind, size, date and download URL.
  Screenshots are not analyzed (their name says nothing): they go straight to their folder.
- **With `--with-content`** the first 1,200 characters of each file are sent too: plain text, documents
  (`textutil`), PDFs and the **OCR of images**, which runs locally with Vision. A screenshot of your online
  banking includes your balance: use it knowing that.
- `.env`, `informes/` and `movimientos.jsonl` hold your key and your file names; they are in `.gitignore`.

Cost: Jev charges for input tokens (USD 0.042 per million at the time of writing); a few hundred files cost cents.

## Move new screenshots automatically (optional)

`com.clasificador.capturas.plist` is a LaunchAgent that runs `--screenshots-only` (no API) whenever the Desktop changes:

```bash
sed "s#/Users/TU_USUARIO#$HOME#g" com.clasificador.capturas.plist > ~/Library/LaunchAgents/com.clasificador.capturas.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.clasificador.capturas.plist
```

The first time, macOS may ask permission for `python3` to access the Desktop. To uninstall:
`launchctl bootout gui/$(id -u)/com.clasificador.capturas` and delete the `.plist`.

Alternative without a script: `defaults write com.apple.screencapture location ~/Desktop/Screenshots`.

## Limitations

- macOS only.
- Processes the loose files of one folder; it does not move folders or recurse into subfolders.
- The thresholds are a reasonable starting point, not the truth: run with `--dry`, look at the CSV and tune them.
- A file's text is untrusted content: a file could be written to confuse the model. The worst outcome is that
  it lands in a different folder.
- Typed answers guarantee the format, not that the judgment is right. That is why nothing is deleted automatically.

## License

[MIT](LICENSE)

# Jev Tidy my Desktop

[English](README.md) · [Español](README.es.md) · **Français** · [Italiano](README.it.md)

Range le Bureau de macOS avec [TypeSafe](https://docs.typesafe.ai). Pour chaque fichier isolé, il décide :

| Décision | Qui la prend |
| --- | --- |
| **Les captures d’écran vont toujours dans le dossier des captures** (`Captures/`, ou celui de votre choix) | Le code (nom du fichier ou métadonnée macOS qui survit aux renommages) |
| **Dossier** de destination | TypeSafe (`Choice`) ; confiance faible → `A_Verifier/` |
| **Sous-dossier** dans ce dossier | TypeSafe (`Choice`) |
| **Importance**, de jetable à « à ne surtout pas perdre » | TypeSafe (`Score` 0–3) |
| Contient-il des **données sensibles** ? Est-il **remplaçable** ? **Temporaire** ? | TypeSafe (`Noul`, une probabilité chacune) |
| Est-ce un **doublon exact** ? | Le code (SHA-256) |
| **Verdict** : conserver / classer / supprimable | Le code, en combinant tout ce qui précède avec des seuils modifiables |

**Il ne supprime jamais rien.** Les candidats à la suppression sont mis de côté dans `A_Supprimer/` pour que
vous les vérifiiez, chaque exécution peut être annulée avec `--undo`, et un rapport CSV conserve tous les scores.

Les noms de dossiers et les messages existent en **anglais, espagnol, français et italien**.

## Prérequis

- macOS (utilise `xattr`, `mdls`, `textutil` et, pour lire images et PDF, Vision et PDFKit)
- Python 3.9+ — aucune dépendance, le `python3` du système suffit
- Une clé d’API TypeSafe : https://console.typesafe.ai/
- Facultatif : Xcode Command Line Tools (`swiftc`) pour l’OCR de `--with-content`

## Installation

Le script range **le dossier qui contient son propre dossier** : clonez-le donc dans le Bureau.

```bash
cd ~/Desktop
git clone https://github.com/sirviejo/jev-tidy-my-desktop.git
cd jev-tidy-my-desktop
cp .env.example .env    # puis renseignez TYPESAFE_API_KEY
```

## Utilisation

```bash
python3 clasificar.py --dry               # montre ce qui se passerait, sans rien déplacer
python3 clasificar.py                     # classe et déplace
python3 clasificar.py --with-content      # lit aussi chaque fichier (voir « Confidentialité »)
python3 clasificar.py --folder Captures   # analyse les fichiers isolés d’un dossier du Bureau
python3 clasificar.py --limit 20          # seulement les 20 premiers fichiers
python3 clasificar.py --screenshots-only  # déplace seulement les captures ; n’appelle jamais l’API
python3 clasificar.py --screenshots-dir "~/Pictures/Captures"   # autre dossier pour les captures
python3 clasificar.py --lang fr           # langue des dossiers et des messages
python3 clasificar.py --undo              # annule la dernière exécution
```

Exemple de sortie :

```
Analyse de 3 fichiers…
  Finances_Facturation/Factures_Emises/ ← INVOICE_0042_ACME.pdf  (conserver: important + données sensibles · importance 3.0/3)
  Captures/Cartes_Lieux/                ← Capture d’écran 2026-04-22 à 20.30.18.png  (classer: aucune raison de supprimer ni de signaler · importance 0.8/3)
  A_Supprimer/                          ← Zoom-installer.dmg  (supprimer: remplaçable · importance 0.1/3)

Résumé
  3 fichiers déplacés :
       1 → Finances_Facturation/Factures_Emises/
       1 → Captures/Cartes_Lieux/
       1 → A_Supprimer/
  Jev : 1 requêtes · 36 questions · 21,480 tokens d’entrée · coût ≈ 0.0009 USD
  Importants (conserver) : 1 · Classés : 1 · Supprimables : 1 (mis de côté dans A_Supprimer/, rien n’est supprimé automatiquement)
  Rapport : informes/informe-20260919-125152.csv
```

## Langues

Les noms de dossiers, de sous-dossiers et les messages viennent de `locales/<langue>.json`. **en, es, fr, it**
sont fournis. Choisissez avec `--lang`, avec `LANGUAGE` dans `.env`, ou laissez le script suivre la langue du
système (avec repli sur l’anglais s’il n’y a pas de traduction).

Tout ce que lit le modèle (questions, descriptions des catégories, identifiants d’options) est toujours en
anglais : la langue choisie **ne change donc pas les jugements**, seulement le nom des dossiers. Les captures
sont reconnues par leur nom dans les quatre langues et, dans toute autre, par la métadonnée macOS.

Pour ajouter une langue : copiez `locales/en.json` vers `locales/<code>.json`, traduisez les valeurs (pas les
clés) et conservez les `%s` / `%d` des messages.

## Fonctionnement

Le code pilote le déroulement ; le modèle ne répond qu’à de petites questions typées.

1. **Les règles fixes d’abord.** Captures et doublons exacts sont détectés sans IA.
2. **Une requête par lot de fichiers.** Toutes les questions sur tous les fichiers du lot partent ensemble et
   sont traitées en parallèle. Les questions de sous-dossier sont *spéculatives* : on en pose une par dossier
   possible (« en supposant qu’il aille dans X, quel sous-dossier ? ») et le code ne lit que celle du dossier
   retenu, au lieu de faire une seconde requête.
3. **La politique vit dans le code.** Le verdict combine les scores avec des seuils placés au début de
   `clasificar.py` (`MIN_CONFIDENCE`, `IMPORTANT_FROM`, `DELETABLE_MIN_SIGNAL`…). Les modifier ne demande pas
   de rappeler l’API : les scores bruts sont dans le CSV.

| Verdict | Règle |
| --- | --- |
| `keep` / conserver | importance ≥ 2.0 **ou** probabilité de données sensibles ≥ 0.7 |
| `delete` / supprimer → `A_Supprimer/` | doublon exact, **ou** importance ≤ 1.0 et (remplaçable ou temporaire) ≥ 0.6 |
| `file` / classer | tout le reste : va dans son dossier sans marque particulière |

Les dossiers déjà présents sur votre Bureau sont proposés automatiquement comme options (décrits par leur
contenu), tout comme les sous-dossiers existants. Les catégories et sous-dossiers prédéfinis se trouvent dans
`CATEGORIES` et `SUBFOLDERS` (descriptions en anglais, pour le modèle) et leurs noms dans `locales/` :
adaptez-les à votre façon de travailler.

## Configuration (`.env`)

| Variable | |
| --- | --- |
| `TYPESAFE_API_KEY` | Obligatoire. |
| `OWNER` | Facultatif. Qui vous êtes (`Ma Société / Mon Nom`), pour distinguer factures émises et reçues. |
| `LANGUAGE` | Facultatif. `en`, `es`, `fr` ou `it`. Par défaut, la langue du système. |
| `SCREENSHOTS_DIR` | Facultatif. Dossier des captures : un nom dans le Bureau ou n’importe quel chemin (`~/Pictures/Captures`). `--screenshots-dir` est prioritaire. |
| `REVIEW_DIR`, `TO_DELETE_DIR` | Facultatif. Renommer les dossiers de vérification et de candidats à la suppression. |
| `IGNORE` | Facultatif. Fichiers ou dossiers du Bureau, séparés par des virgules, à ne pas toucher ni proposer comme destination. |

## Confidentialité

- **Par défaut**, seules des métadonnées sont envoyées à TypeSafe : nom, extension, type, taille, date et URL
  de téléchargement. Les captures ne sont pas analysées (leur nom ne dit rien) : elles vont directement dans
  leur dossier.
- **Avec `--with-content`**, les 1 200 premiers caractères de chaque fichier sont envoyés aussi : texte brut,
  documents (`textutil`), PDF et **OCR des images**, réalisé localement avec Vision. Une capture de votre
  banque en ligne contient votre solde : utilisez l’option en connaissance de cause.
- `.env`, `informes/` et `movimientos.jsonl` contiennent votre clé et les noms de vos fichiers ; ils sont dans
  `.gitignore`.

Coût : Jev facture les tokens d’entrée (0,042 USD par million au moment de la rédaction) ; quelques centaines
de fichiers coûtent quelques centimes.

## Déplacer automatiquement les nouvelles captures (facultatif)

`com.clasificador.capturas.plist` est un LaunchAgent qui lance `--screenshots-only` (sans API) dès que le Bureau change :

```bash
sed -e "s#__DIR__#$PWD#g" -e "s#__DESKTOP__#$(dirname "$PWD")#g" com.clasificador.capturas.plist > ~/Library/LaunchAgents/com.clasificador.capturas.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.clasificador.capturas.plist
```

La première fois, macOS peut demander l’autorisation pour que `python3` accède au Bureau. Pour désinstaller :
`launchctl bootout gui/$(id -u)/com.clasificador.capturas` puis supprimez le `.plist`.

Alternative sans script : `defaults write com.apple.screencapture location ~/Desktop/Captures`.

## Limites

- macOS uniquement.
- Traite les fichiers isolés d’un dossier ; ne déplace pas les dossiers et n’explore pas les sous-dossiers.
- Les seuils sont un point de départ raisonnable, pas une vérité : lancez avec `--dry`, regardez le CSV et ajustez-les.
- Le texte d’un fichier est un contenu non fiable : un fichier pourrait être rédigé pour tromper le modèle.
  Au pire, il atterrit dans un autre dossier.
- Les réponses typées garantissent le format, pas la justesse du jugement. C’est pourquoi rien n’est supprimé
  automatiquement.

## Licence

[MIT](LICENSE)

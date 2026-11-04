# Specifica: Convertitore DOCX per AiGrader

## Contesto

AiGrader è un correttore automatico di elaborati scolastici basato su AI. Il sistema rileva i file nella cartella `consegne/`, li converte in Markdown tramite convertitori specifici per estensione, e passa il Markdown all'LLM per la valutazione.

Il convertitore per `.docx` è già mappato in `config/settings.py` (`".docx": "converters.docx_converter"`) ma **il file del modulo non esiste ancora**. Il convertitore `.doc` (legacy) dipende da questo convertitore: converte `.doc` → `.docx` temporaneo → invoca `docx_converter.converti()`.

## Requisiti Funzionali

### 1. Input/Output

- **Input**: Percorso a un file `.docx` (formato Office Open XML)
- **Output**: Stringa Markdown strutturata per l'analisi LLM

### 2. Struttura del Formato DOCX

Un file `.docx` è un archivio ZIP contenente una struttura XML. I file XML rilevanti sono:

| Percorso nel ZIP | Descrizione |
|---|---|
| `word/document.xml` | **Contenuto principale** del documento (testo, paragrafi, tabelle, immagini) |
| `word/footnotes.xml` | Note a piè di pagina |
| `word/endnotes.xml` | Note di chiusura |
| `word/comments.xml` | Commenti |
| `word/header{N}.xml` | Intestazioni di pagina |
| `word/footer{N}.xml` | Piè di pagina |
| `word/media/` | Immagini embedded (riferite nel documento) |
| `docProps/core.xml` | Metadati core (autore, titolo, data) |
| `docProps/app.xml` | Metadati applicazione |
| `word/settings.xml` | Impostazioni del documento |

### 3. Pipeline di Conversione

```
.file.docx
    │
    ▼ (zipfile)
Scompatta in memoria/temp
    │
    ├─→ word/document.xml ──────────────────────────────────┐
    ├─→ word/footnotes.xml                                  │
    ├─→ word/endnotes.xml                                   │  Parsing XML
    ├─→ word/comments.xml                                   │  (xml.etree.ElementTree)
    ├─→ word/header{N}.xml ─────────────────────────────────┤
    ├─→ word/footer{N}.xml ─────────────────────────────────┤
    ├─→ docProps/core.xml ──────────────────────────────────┤
    ├─→ docProps/app.xml ───────────────────────────────────┤
    └─→ word/settings.xml ──────────────────────────────────┘
    │
    ▼
Unificazione in Markdown strutturato
    │
    ▼
Restituzione stringa Markdown
```

### 4. Output Markdown Atteso

Il Markdown generato deve seguire questa struttura:

```markdown
# [Titolo del Documento]

## Metadati

- **Autore**: [nome]
- **Creato**: [data]
- **Modificato**: [data]

## Contenuto Principale

[Contenuto di document.xml convertito in Markdown]

## Note a Piè di Pagina

[Contenuto di footnotes.xml]

## Note di Chiusura

[Contenuto di endnotes.xml]

## Commenti

[Contenuto di comments.xml]

## Intestazioni

[Contenuto di header{N}.xml]

## Piè di Pagina

[Contenuto di footer{N}.xml]
```

### 5. Estensibilità

Il convertitore deve essere progettato per consentire l'aggiunta di nuovi parser XML in futuro senza modifiche all'architettura centrale. Si raccomanda un pattern a strategy/plugin dove ogni parser XML è una funzione indipendente registrata in una mappa.

**Estensioni future previste**:
- `word/styles.xml` → stili applicati (per analisi formattazione)
- `word/numbering.xml` → strutture di numerazione
- `word/comments.xml` → estrazione completa commenti
- `word/footnotes.xml` → note con riferimenti
- `word/webSettings.xml` → impostazioni web
- `word/theme.xml` → tema del documento

## Requisiti Tecnici

### 1. Dipendenze

- **Nessuna dipendenza esterna**: utilizzare solo librerie standard Python
  - `zipfile` → apertura archivio ZIP
  - `xml.etree.ElementTree` → parsing XML
  - `re` → espressioni regolari per pulizia testo
  - `pathlib` → manipolazione percorsi
  - `tempfile` → directory temporanea per estrazione
  - `shutil` → gestione file temporanei

### 2. Namespace XML

I file DOCX utilizzano namespace XML. Il namespace principale è:

```
http://schemas.openxmlformats.org/wordprocessingml/2006/main
```

Tutti i parser XML devono gestire correttamente i namespace prefix.

### 3. Gestione Immagini

Le immagini in `word/media/` sono riferite da `document.xml` tramite elementi `<a:blip>`. Il convertitore deve:
- Rilevare le immagini presenti
- Segnalare la loro presenza nel Markdown con un placeholder
- **Non** convertire le immagini in base64 (troppo pesante per LLM)

Placeholder immagine:
```markdown
![Immagine: [nome_file]](embedded)
```

### 4. Gestione Tabelle

Le tabelle DOCX devono essere convertite in tabelle Markdown:

```markdown
| Col 1 | Col 2 | Col 3 |
|-------|-------|-------|
| Val 1 | Val 2 | Val 3 |
```

### 5. Gestione Stili di Paragrafo

I paragrafi DOCX hanno stili associati (Heading 1, Heading 2, Normal, ecc.). Il convertitore deve mappare gli stili in Markdown:

| Stile DOCX | Markdown |
|---|---|
| Heading 1 | `# Testo` |
| Heading 2 | `## Testo` |
| Heading 3 | `### Testo` |
| Heading 4 | `#### Testo` |
| Heading 5 | `##### Testo` |
| Heading 6 | `###### Testo` |
| Normal | Testo normale |
| Quote | `> Testo` |

### 6. Gestione Formattazione Inline

- **Grassetto** (`<w:b>`) → `**testo**`
- **Corsivo** (`<w:i>`) → `*testo*`
- **Sottolineato** (`<w:u>`) → `_testo_`
- **Barrato** (`<w:strike>`) → `~~testo~~`
- **Pedice** (`<w:vertAlign w:val="subscript">`) → `<sub>testo</sub>`
- **Apice** (`<w:vertAlign w:val="superscript">`) → `<sup>testo</sup>`
- **Colori** → non convertire (troppo verboso)

## Struttura del Codice

### File: `converters/docx_converter.py`

```python
"""Convertitore DOCX → Markdown per AiGrader.

Converte un file Microsoft Word (.docx) in Markdown strutturato
per l'analisi LLM. Utilizza esclusivamente librerie standard Python.

Pipeline:
1. Scompatta il file .docx (ZIP)
2. Parsea i file XML rilevanti
3. Unifica il contenuto in Markdown strutturato
4. Restituisce la stringa Markdown
"""

from pathlib import Path
from typing import Optional


# ===== CONFIGURAZIONE =====
NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
IMAGE_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/drawing"
PIC_NS = "http://schemas.openxmlformats.org/pictureMetadata/2006/main"


# ===== PARSER XML (ESTENSIBILE) =====
# Ogni parser è una funzione indipendente che riceve l'ElementTree root
# e restituisce una stringa Markdown.
# Per aggiungere un nuovo parser:
#   1. Crea la funzione _parse_nome_elemento(root, ns) -> str
#   2. Aggiungi la voce in PARSER_REGISTRY

PARSER_REGISTRY: dict[str, dict] = {
    "document": {
        "path": "word/document.xml",
        "parser": "_parse_document",
        "section_title": "Contenuto Principale",
    },
    "footnotes": {
        "path": "word/footnotes.xml",
        "parser": "_parse_footnotes",
        "section_title": "Note a Piè di Pagina",
    },
    "endnotes": {
        "path": "word/endnotes.xml",
        "parser": "_parse_endnotes",
        "section_title": "Note di Chiusura",
    },
    "comments": {
        "path": "word/comments.xml",
        "parser": "_parse_comments",
        "section_title": "Commenti",
    },
}


# ===== FUNZIONI DI PARSING =====

def _parse_document(root: object, ns: str) -> str:
    """Parsea word/document.xml e restituisce Markdown."""
    ...


def _parse_footnotes(root: object, ns: str) -> str:
    """Parsea word/footnotes.xml e restituisce Markdown."""
    ...


def _parse_endnotes(root: object, ns: str) -> str:
    """Parsea word/endnotes.xml e restituisce Markdown."""
    ...


def _parse_comments(root: object, ns: str) -> str:
    """Parsea word/comments.xml e restituisce Markdown."""
    ...


# ===== FUNZIONI DI UTILITÀ =====

def _extract_text_from_run(run_element: object) -> str:
    """Estrae il testo da un elemento <w:r> (run) applicando la formattazione inline."""
    ...


def _get_paragraph_style(paragraph: object, ns: str) -> str:
    """Estrae il nome dello stile di un paragrafo."""
    ...


def _style_to_markdown_prefix(style_name: str) -> str:
    """Converte un nome stile DOCX in prefisso Markdown."""
    ...


def _extract_images_from_document(root: object, ns: str) -> list[str]:
    """Estrae i nomi delle immagini referenziate nel documento."""
    ...


# ===== FUNZIONE PRINCIPALE =====

def converti(file_path: str | Path) -> str:
    """Converte un file DOCX in Markdown strutturato.

    Pipeline:
    1. Validazione del file
    2. Estrazione dell'archivio ZIP in directory temporanea
    3. Parsing dei file XML tramite PARSER_REGISTRY
    4. Estrazione metadati da docProps
    5. Unificazione del contenuto in Markdown
    6. Pulizia risorse temporanee

    Args:
        file_path: Percorso al file .docx.

    Returns:
        Stringa Markdown strutturata con tutto il contenuto del documento.

    Raises:
        IOError: Se il file non esiste, non è leggibile, o è corrotto.
    """
    ...
```

## Interfaccia con il Resto del Sistema

### 1. Integrazione con `converters/__init__.py`

Il modulo deve esportare una funzione `converti(file_path)` che:
- Accetta `str | Path` come argomento
- Restituisce una `str` con contenuto Markdown
- Segue lo stesso pattern degli altri convertitori

### 2. Integrazione con `config/settings.py`

La mappatura è già presente:
```python
".docx": "converters.docx_converter",
```

### 3. Integrazione con `main.py`

Il convertitore è chiamato da `_processa_studente_folder()` e
`_processa_studente_multi_file()` tramite `get_converter()`.

### 4. Integrazione con `doc_converter.py`

`doc_converter.py` importa e invoca `docx_converter.converti()` dopo
la conversione `.doc` → `.docx` tramite Aspose.Words.

## Criteri di Accettazione

- [ ] Il convertitore accetta file `.docx` validi e restituisce Markdown
- [ ] Il contenuto principale (`document.xml`) è correttamente convertito
- [ ] Testi in grassetto, corsivo, sottolineato, barrato sono preservati
- [ ] Intestazioni (Heading 1-6) sono convertite in `#` Markdown
- [ ] Tabelle sono convertite in tabelle Markdown
- [ ] Note a piè di pagina e note di chiusura sono estratte
- [ ] Commenti sono estratti e formattati
- [ ] Metadati (autore, titolo, date) sono estratti da `docProps`
- [ ] Immagini sono segnalate con placeholder
- [ ] Il convertitore gestisce file corrotti con eccezione `IOError`
- [ ] Il convertitore gestisce file `.docx` vuoti
- [ ] Il convertitore non lascia file temporanei in caso di errore
- [ ] Il convertitore è estensibile: nuovi parser XML possono essere aggiunti
  modificando solo `PARSER_REGISTRY` e aggiungendo funzioni `_parse_*`
- [ ] Il convertitore non richiede dipendenze esterne (solo stdlib)
- [ ] Il convertitore passa la funzione `doc_converter.py` senza errori

## Rischi e Considerazioni

1. **Namespace XML**: I file DOCX utilizzano namespace complessi. Il parsing
   deve gestire correttamente i prefissi.

2. **Performance**: File DOCX molto grandi con molte immagini possono essere
   lenti da processare. Considerare limiti di dimensione in futuro.

3. **Compatibilità**: Il formato DOCX è standard ISO/IEC 29500. Il convertitore
   dovrebbe funzionare con file creati da Microsoft Word, LibreOffice, Google Docs.

4. **Estensibilità**: La `PARSER_REGISTRY` permette di aggiungere nuovi parser
   senza modificare la logica centrale. Ogni parser è una funzione pura che
   riceve l'ElementTree root e restituisce una stringa.

5. **Dipendenze**: Il convertitore `.doc` (`doc_converter.py`) dipende da
   Aspose.Words per la conversione `.doc` → `.docx`. Il convertitore `.docx`
   stesso non richiede dipendenze esterne.

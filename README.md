# AiGrader

AiGrader è un tool Python modulare che automatizza la valutazione di prove scolastiche. Accetta in input tracce di compiti (in markdown) e consegne degli studenti (in xlsx, docx, html), genera una griglia di valutazione tramite LLM, corregge ogni elaborato individualmente e produce un riepilogo finale.

## Requisiti

- **Python 3.10+**
- **Server LLM locale** (es. Ollama, LM Studio, vLLM) compatibile con l'API OpenAI
- Dipendenze elencate in `requirements.txt`

## Installazione

```bash
git clone <repository-url>
cd AiGrader
python -m venv venv
source venv/bin/activate  # venv\Scripts\activate su Windows
pip install -r requirements.txt
```

## Configurazione

La configurazione è centralizzata in `config/settings.py`. È possibile modificare i parametri del modello (LLM_MODEL), l'URL del server (LLM_BASE_URL) e altre impostazioni di rendering.

## Utilizzo

AiGrader supporta l'elaborazione di un singolo compito o l'esecuzione in batch su tutta la cartella `elaborazioni/`.

### Modalità di funzionamento

#### Modalità singolo compito

```bash
python main.py "elaborazioni/NomeCompito"
```

La pipeline parte dalla cartella del compito indicata come argomento: elabora la `traccia.md`, genera (o normalizza) la griglia di valutazione, converte ed elabora le consegne presenti in `consegne/` e scrive le correzioni in `correzioni/`.

#### Modalità batch

```bash
python main.py --batch
```

Elabora automaticamente tutte le sottocartelle di `elaborazioni/` una per una. Le cartelle già completate vengono saltate tramite il registro `elaborazioni/report.log`.

### Tag disponibili in input (CLI)

AiGrader accetta i seguenti tag come argomenti da riga di comando:

| Tag | Tipo | Descrizione |
|---|---|---|
| `<PERCORSO_CARTELLA>` | argomento posizione (opzionale) | Percorso della cartella del compito da elaborare (modalità singolo, default) |
| `--batch` | flag booleano | Elabora tutti i compiti nella cartella `elaborazioni/` |
| `--clear` | flag booleano | Rilancia l'elaborazione anche dei compiti già completati, pulendo correzioni precedenti (`correzioni/`, `tmp/`, `griglia_valutazione.md`, `riepilogo.md`) |
| `--help` | flag booleano | Mostra l'help con esempi d'uso |

Esempi d'uso:

```bash
python main.py                                  # senza argomenti → mostra usage error
python main.py --help                           # mostra aiuto
python main.py "elaborazioni/4RIM - Verifica pratica di laboratorio_ Google Sheets e Google Docs"  # singolo
python main.py --batch                         # tutti i compiti
python main.py --batch --clear                # tutti i compiti, incluso ripartire da zero
```

### Struttura di un Compito

All'interno di `elaborazioni/`, ogni cartella compito deve contenere il **set minimo di file** che il software si aspetta di trovare:

- **`traccia.md`**: Testo del compito.
- **`consegne/`**: Cartella contenente i file degli studenti (formati supportati: .xlsx, .docx, .doc, .html). Ogni file deve seguire lo standard `nome_studente - nome_file.ext` (es. `Rossi Mario - bilancio.xlsx`).
- **`correzioni/`**: Cartella di output generata automaticamente.
- **`griglia.md`**: Griglia di valutazione fornita dal docente (opzionale; se assente, viene generata automaticamente).

Eventuali allegati della traccia possono essere presenti nella stessa cartella del compito e vengono richiamati tramite la sintassi:

```
[[allegato: nome_allegato.ext]]
```

dove `nome_allegato.ext` è l'estensione del file allegato in un formato supportato dai convertitori (es. `traccia_allegato.xlsx`). Se un allegato è presente, la pipeline lo converte in markdown e lo passa al prompt di correzione come contesto aggiuntivo.

*Nota: I prompt di sistema sono gestiti centralmente nella cartella `prompts/`.*

### Flusso di Esecuzione

1. **Generazione griglia**: Analizza `traccia.md` e genera `griglia_valutazione.md` (tramite `prompts/prompt_griglia.md`), oppure normalizza la griglia fornita dal docente tramite `prompts/prompt_griglia_custom.md`.
2. **Correzione**: Per ogni studente, converte il file in markdown e applica il prompt di correzione (`prompts/prompt_correzione_*.md`).
3. **Riepilogo**: Genera il riepilogo finale (`prompts/prompt_riepilogo.md`).

## Personalizzazioni tramite i file in `prompts/`

Tutte le istruzioni che guidano l'LLM sono contenute in file Markdown dedicati, riservati alla cartella `prompts/`. Modificare questi file permette di personalizzare il comportamento di AiGrader **senza toccare il codice**.

### Prompt disponibili

| Prompt | Scopo | Placeholder principali |
|---|---|---|
| `prompt_griglia.md` | Generazione automatica della griglia di valutazione a partire dalla traccia | `{traccia}` |
| `prompt_griglia_custom.md` | Normalizzazione della griglia di valutazione fornita dal docente (senza modificarne criteri e punteggi) | `{traccia}`, `{griglia}` |
| `prompt_correzione_senza_allegati.md` | Correzione di un elaborato senza allegati alla traccia | `{nome_studente}`, `{traccia}`, `{griglia}`, `{dati_studente}` |
| `prompt_correzione_con_allegati.md` | Correzione di un elaborato con allegati di tipo misto o senza prompt specializzato | `{nome_studente}`, `{traccia}`, `{allegato_originale}`, `{griglia}`, `{dati_studente}` |
| `prompt_correzione_con_allegati_xlsx.md` | Correzione di un elaborato con allegati in fogli di calcolo (`.xlsx`) | `{nome_studente}`, `{traccia}`, `{allegato_originale}`, `{griglia}`, `{dati_studente}` |
| `prompt_correzione_con_allegati_docx.md` | Correzione di un elaborato con allegati in documenti di testo (`.docx`, `.doc`) | `{nome_studente}`, `{traccia}`, `{allegato_originale}`, `{griglia}`, `{dati_studente}` |
| `prompt_riepilogo.md` | Generazione del riepilogo finale delle valutazioni di tutti gli studenti | `{tutte_le_correzioni}` |

### Come personalizzare

- **Griglia**: modificare `prompt_griglia.md` (metodo di generazione automatica) o `prompt_griglia_custom.md` (metodo di normalizzazione della griglia del docente). Cambiare il numero di criteri, i livelli di prestazione, le percentuali di punteggio, il linguaggio.
- **Correzione**: modificare i quattro prompt di correzione per cambiare scala di ancoraggio, obblighi di citazione, criteri da non valutare, tono. Attenzione: la scala di ancoraggio è condivisa e va mantenuta coerente tra i prompt.
- **Riepilogo**: modificare `prompt_riepilogo.md` per cambiare le statistiche, il formato delle tabelle e le osservazioni generali.

Per un dettaglio completo su ogni prompt, sui placeholder disponibili e sulle procedure di estensione (es. aggiunta di un nuovo tipo di allegato), si rimanda alla documentazione nella cartella `prompts/` (`prompts/README.md`).

## Formati supportati

- **Excel** (.xlsx) — Convertito in tabelle markdown.
- **Word** (.docx, .doc) — Convertito in markdown (struttura titoli, tabelle, liste).
- **Web** (.html, .htm) — Convertito in markdown.
- **Archivi** (.zip) — Supporto per l'estrazione.

## Struttura del repository

```
AiGrader/
├── main.py               # Script orchestratore principale
├── config/               # Configurazione parametrica globale (settings.py)
├── converters/           # Moduli di conversione file → markdown
├── prompts/              # Template di prompt con placeholder
├── utils/                # Connessione LLM e health check
├── elaborazioni/         # Compiti in elaborazione (una cartella per compito)
└── README.md
```

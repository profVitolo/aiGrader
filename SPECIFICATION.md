# AiGrader - Specifiche di Implementazione

## Panoramica

AiGrader è un tool Python modulare che automatizza la valutazione di prove scolastiche tramite un modello LLM locale. Accetta in input tracce di compiti (in markdown) e consegne degli studenti (in xlsx, docx, html), genera una griglia di valutazione e corregge ogni elaborato individualmente, producendo un riepilogo finale.

## Architettura

```
AiGrader/
├── main.py                       # Orchestratore principale
├── config/                       # Configurazione parametrica
│   ├── __init__.py
│   └── settings.py
├── prompts/                      # Template prompt per LLM
│   ├── prompt_griglia.md
│   ├── prompt_correzione.md
│   └── prompt_riepilogo.md
├── utils/                        # Utility LLM e health check
│   ├── __init__.py
│   └── llm_client.py
├── converters/                   # Convertitori formato → markdown
│   ├── __init__.py
│   ├── xlsx_converter.py
│   ├── docx_converter.py
│   └── web_converter.py
└── elaborazioni/                 # Contenitore compiti
    ├── <nome_compito>/
│       ├── traccia.md
│       ├── griglia_valutazione.md
│       ├── riepilogo.md
│       ├── consegne/
│       └── correzioni/
```

## Componenti

### 1. Configurazione (`config/settings.py`)

Deve contenere tutti i parametri configurabili:
- `LLM_BASE_URL`: URL del server LLM locale (default: `http://localhost:8080/v1`)
- `LLM_API_KEY`: Chiave API (default: `sk-no-key-required`)
- `LLM_MODEL`: Nome del modello (default: `qwen`)
- `LLM_TEMPERATURE`: Temperatura di generazione (default: `0.2`)
- `PROJECT_ROOT`: Radice del progetto
- `CONVERTERS_MAP`: Mappatura estensioni → moduli convertitore

### 2. LLM Client (`utils/llm_client.py`)

Modulo dedicato alla connessione con il modello LLM:
- `create_client()`: istanzia il client OpenAI con configurazione da settings
- `call_llm(messages, model=None, temperature=None)`: invia messaggi e riceve risposta
- `health_check()`: verifica che il LLM sia raggiungibile
- Funzione `call_llm_with_system_prompt(system_prompt, user_prompt)`: helper per prompt con ruolo system

### 3. Convertitori (`converters/`)

Ogni convertitore espone una funzione `converti(file_path: Path) -> str` che restituisce il contenuto in markdown:

#### `xlsx_converter.py`
- Legge il file Excel con `openpyxl`
- Per ogni foglio, genera una tabella markdown con coordinate e valori (formula e valore)
- Gestione errori per file corrotti o protetti

#### `docx_converter.py`
- Legge il documento Word con `python-docx`
- Estrae paragrafi mantenendo la struttura dei titoli (Heading styles)
- Gestisce tabelle presenti nel documento

#### `web_converter.py`
- Legge file HTML con `beautifulsoup4`
- Estrae il testo mantenendo la struttura (titoli, liste, tabelle)
- Rimuove script e stile non necessari

#### `__init__.py`
- Esporta le funzioni di conversione
- Fornisce `get_converter(extension) -> callable | None`

### 4. Prompt Templates (`prompts/`)

#### `prompt_griglia.md`
Template per generare la griglia di valutazione. Riceve la traccia del compito e produce una griglia strutturata in markdown con:
- Criteri di valutazione chiari
- Punteggi assegnati per ogni criterio
- Livelli di prestazione (es. eccellente, buono, sufficiente, insufficiente)

Input: `{traccia}` - contenuto di traccia.md

#### `prompt_correzione.md`
Template per correggere il lavoro di uno studente. Riceve:
- Nome dello studente
- La griglia di valutazione generata
- Il contenuto convertito in markdown della consegna

Output: valutazione strutturata con:
- Punteggio per ogni criterio
- Totale ottenuto
- Commenti e osservazioni

Input: `{nome_studente}`, `{griglia}`, `{dati_studente}`

#### `prompt_riepilogo.md`
Template per il riepilogo finale. Riceve tutte le correzioni e produce:
- Nome del compito (dalla traccia)
- Lista studenti con punteggi individuali
- Statistiche (media, massimo, minimo)
- Eventuali note generali

Input: `{tutte_le_correzioni}` - concatenazione di tutte le correzioni

### 5. Orchestratore (`main.py`)

Il main.py deve essere refattorizzato per:
- Usare `config/settings.py` per la configurazione
- Usare `utils/llm_client.py` per le chiamate LLM
- Usare `converters/__init__.py` per il dispatcher dei convertitori
- Supportare la modalità "compito singolo" (working directory = `<elaborazioni/<compito>/`)
- Supportare la modalità "batch" (scansiona tutte le sottocartelle di `elaborazioni/`)

Flusso per compito:
1. Leggere `traccia.md`
2. Generare `griglia_valutazione.md` tramite LLM
3. Per ogni file in `consegne/`:
   - Convertire in markdown
   - Correggere tramite LLM
   - Salvare in `correzioni/`
4. Generare `riepilogo.md` con tutte le valutazioni

## Requisiti Tecnici

- Python 3.10+
- Dipendenze: `openai`, `openpyxl`, `python-docx`, `beautifulsoup4`
- Nessuna dipendenza pesante (niente pandas, numpy, ecc.)
- Codice modulare e testabile
- Error handlingGraceful per ogni fase del pipeline

## Flusso di Esecuzione

```
Per ogni compito in elaborazioni/:
  1. main.py entra nella cartella del compito
  2. Legge traccia.md
  3. Genera griglia_valutazione.md
  4. Per ogni studente in consegne/:
     a. Converti file → markdown
     b. Correggi con LLM → griglia individuale
     c. Salva in correzioni/
  5. Genera riepilogo.md
```

## Note Implementative

- Il nome del file dello studente (es. "rossi mario - bilancio.xlsx") determina il nome dello studente
- I convertitori devono gestire file vuoti o corrotti con messaggi di errore chiari
- La griglia di valutazione è condivisa per tutti gli studenti dello stesso compito
- Ogni studente ha una propria griglia di correzione individuale

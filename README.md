# AiGrader

Correzione automatica di compiti scolastici tramite modello LLM locale.

## Panoramica

AiGrader è un tool Python modulare che automatizza la valutazione di prove scolastiche. Accetta in input tracce di compiti (in markdown) e consegne degli studenti (in xlsx, docx, html), genera una griglia di valutazione tramite LLM, corregge ogni elaborato individualmente e produce un riepilogo finale con punteggi e statistiche.

## Requisiti

- **Python 3.10+**
- **Server LLM locale** (es. Ollama, LM Studio, vLLM) compatibile con l'API OpenAI
- Dipendenze: `openai`, `openpyxl`, `python-docx`, `beautifulsoup4`

## Installazione

```bash
# Clona il repository
git clone <repository-url>
cd AiGrader

# Crea un ambiente virtuale (raccomandato)
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Installa le dipendenze
pip install -r requirements.txt
# oppure
pip install -e .
```

## Configurazione

La configurazione è centralizzata in `config/settings.py`. I valori di default sono:

```python
LLM_BASE_URL = "http://localhost:8080/v1"   # URL del server LLM
LLM_API_KEY = "sk-no-key-required"           # Chiave API (se necessaria)
LLM_MODEL = "Qwen3.6-35B-A3B-UD-Q4_K_XL_MTP"  # Modello da usare
LLM_TEMPERATURE = 0.2                         # Temperatura di generazione
```

Modifica questi valori in base al tuo setup LLM.

## Utilizzo

### Modalità Compito Singolo

Elabora un singolo compito nella working directory corrente:

```bash
python main.py --singolo
```

### Modalità Batch

Elabora tutti i compiti nella cartella `elaborazioni/`:

```bash
python main.py --batch
```

### Mostra aiuto

```bash
python main.py --help
```

## Preparare un Compito

Per ogni compito da correggere, prepara la seguente struttura:

```
<nome_compito>/
├── traccia.md              # Testo del compito da correggere
├── prompt_griglia.md       # Template per generare la griglia di valutazione
├── prompt_correzione.md    # Template per correggere le consegne
├── prompt_riepilogo.md     # Template per il riepilogo finale
├── consegne/               # Cartella con le consegne degli studenti
│   ├── rossi mario - bilancio.xlsx
│   ├── bianchi luca - bilancio.docx
│   └── nerianna - bilancio.html
└── correzioni/             # Cartella di output (creata automaticamente)
    ├── rossi mario - bilancio - griglia.md
    └── bianchi luca - bilancio - griglia.md
```

### File richiesti

1. **traccia.md** — Il testo del compito assegnato agli studenti
2. **prompt_griglia.md** — Template per generare la griglia di valutazione (riceve `{traccia}`)
3. **prompt_correzione.md** — Template per correggere le consegne (riceve `{nome_studente}`, `{griglia}`, `{dati_studente}`)
4. **prompt_riepilogo.md** — Template per il riepilogo finale (riceve `{tutte_le_correzioni}`)
5. **consegne/** — Cartella contenente i file degli studenti (formati supportati: xlsx, docx, html)

### Formati supportati

- **Excel** (.xlsx) — Convertito in tabelle markdown
- **Word** (.docx) — Convertito in markdown con struttura titoli e tabelle
- **HTML** (.html, .htm) — Convertito in markdown mantenendo titoli e liste

## Flusso di Esecuzione

Per ogni compito, AiGrader esegue:

1. **Generazione griglia** — Legge `traccia.md` e genera `griglia_valutazione.md` tramite LLM
2. **Correzione consegne** — Per ogni studente:
   - Converte il file in markdown
   - Corregge con LLM usando la griglia
   - Salva il risultato in `correzioni/`
3. **Riepilogo** — Genera `riepilogo.md` con punteggi e statistiche

## Struttura del Progetto

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
├── elaborazioni/                 # Contenitore compiti
│   └── <nome_compito>/
│       ├── traccia.md
│       ├── griglia_valutazione.md
│       ├── riepilogo.md
│       ├── consegne/
│       └── correzioni/
├── requirements.txt              # Dipendenze Python
├── pyproject.toml                # Metadati progetto
└── README.md                     # Questo file
```

## Esempio Completo

```bash
# 1. Preparare il compito
mkdir -p elaborazioni/bilancio-2024/consegne
cp traccia.md elaborazioni/bilancio-2024/
cp prompts/*.md elaborazioni/bilancio-2024/
cp studenti/*.xlsx elaborazioni/bilancio-2024/consegne/

# 2. Eseguire la correzione
python main.py --batch
```

## Prompt Templates

I template prompt definiscono il comportamento del LLM. Ecco una panoramica:

### prompt_griglia.md
Genera una griglia di valutazione strutturata con criteri, punteggi e livelli di prestazione.

### prompt_correzione.md
Corregge il lavoro di uno studente producendo un report con punteggio per criterio, totale e commenti.

### prompt_riepilogo.md
Genera un riepilogo con lista studenti, punteggi individuali e statistiche (media, massimo, minimo).

## Note

- Il nome dello studente viene estratto dal nome del file (es. "rossi mario - bilancio.xlsx" → "Rossi Mario")
- La griglia di valutazione è generata una sola volta e condivisa per tutti gli studenti dello stesso compito
- Ogni studente riceve una correzione individuale salvata in `correzioni/`
- File non supportati nella cartella `consegne/` vengono saltati con un messaggio di avviso

## Documentazione Completa

Per le specifiche tecniche dettagliate, consulta [SPECIFICATION.md](SPECIFICATION.md).

## Licenza

Progetto educativo per la correzione automatica di compiti scolastici.

# AiGrader — Sommario del Progetto

## Cos'è AiGrader

AiGrader è una utility a riga di comando in Python che automatizza la correzione di consegne scolastiche sfruttando un LLM (Large Language Model) locale accessibile via API compatibili con l'interfaccia OpenAI. Il software elabora automaticamente tre output:

1. **Una griglia di valutazione** — generata automaticamente dalla traccia del compito, oppure normalizzata nel formato standard se il docente ha fornito una griglia personalizzata (`griglia.md`).
2. **Le correzioni individuali** — per ogni studente, il LLM confronta l'elaborato consegnato con la traccia, gli allegati di partenza e la griglia, producendo un punteggio per criterio, un livello (Eccellente/Buono/Sufficiente/Insufficiente) e un commento motivato.
3. **Un riepilogo finale** — statistica aggregata con punteggio medio, massimo, minimo, range e osservazioni generali sulla classe.

### Flusso di elaborazione

```
Traccia (+ allegati) → Griglia di valutazione → Correzioni per studente → Riepilogo
```

Il sistema supporta due modalità operative:
- **Compito singolo**: `python main.py "elaborazioni/NomeCompito"`
- **Batch**: `python main.py --batch` (elabora tutte le cartelle in `elaborazioni/`)

---

## Struttura dei file

### `config/__init__.py`
Modulo di inizializzazione del pacchetto `config`. Esporta la classe `Settings` per consentirne l'import da altri moduli con `from config import Settings`.

### `config/settings.py`
Definisce la classe `Settings`, contenitore centrale di tutta la configurazione parametrica di AiGrader. Include:
- **Impostazioni LLM**: URL base, API key, nome modello, temperatura e top_p.
- **Soglie e limiti**: lunghezza massima XML per pivot table (`PIVOT_XML_MAX_LENGTH`), soglia minima caratteri per considerare un DOCX "template vuoto" (`DOCX_MIN_CONTENT_CHARS`).
- **Mappa convertitori**: dizionario che associa ogni estensione di file (incluse estensioni composte come `.tar.gz`) al modulo convertitore responsabile.

### `converters/__init__.py`
Contiene la logica di routing dei convertitori. Esporta tre funzioni:
- `get_extension(file_path)`: estrae l'estensione "utile" gestendo correttamente estensioni composte (`.tar.gz`, `.tar.bz2`).
- `is_supported(file_path)`: verifica se esiste un convertitore registrato per il file.
- `get_converter(extension)`: carica dinamicamente il modulo convertitore corrispondente all'estensione, restituendo la funzione `converti()`.

### `converters/archive_converter.py`
Convertitore per archivi compressi (`.zip`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.gz`, `.bz2`). Funzionalità:
- Estrazione sicura con protezione da **path traversal** (zip-slip / tar-slip) e link simbolici malevoli.
- Navigazione ricorsiva della directory estratta per trovare file supportati.
- Invoca i convertitori appropriati per ogni file trovato.
- Restituisce un Markdown che include la struttura ad albero dell'archivio e il contenuto di ogni file convertito.

### `converters/doc_converter.py`
Convertitore per documenti Word legacy (`.doc`, formato binario Word 97-2003). Pipeline a due step:
1. Carica il file `.doc` con **Aspose.Words** (`pip install aspose-words`).
2. Lo salva come `.docx` temporaneo e lo passa a `docx_converter.converti()`.
- Gestisce graceful degradation se Aspose.Words non è installato.

### `converters/docx_converter.py`
Convertitore DOCX → Markdown strutturato. Utilizza **esclusivamente librerie standard Python** (nessuna dipendenza esterna). Funzionalità:
- **Metadati**: estrae autore, titolo, data di creazione/modifica da `core.xml`.
- **Parsing XML**: analizza `document.xml`, `styles.xml`, `numbering.xml`, `footnotes.xml`, `endnotes.xml`, relazioni (`_rels`), intestazioni/piè di pagina.
- **Struttura**: titoli (Heading 1-6, Titolo, Sottotitolo), elenchi puntati/numerati con annidamento, interruzioni di pagina.
- **Sezioni**: orientamento, margini, tipo di interruzione, intestazioni e piè di pagina.
- **Oggetti incorporati**: immagini, grafici, diagrammi SmartArt, forme, caselle di testo, oggetti OLE, video/audio — rilevati e sostituiti con segnaposto testuali.
- **Tabelle**: ricostruite in formato Markdown tabellare.
- **Note**: note a piè di pagina e note di chiusura con riferimenti nel testo.
- **Sommari**: TOC riconosciuti e marcati.
- **Rilevamento template vuoto**: se il contenuto sostanziale è sotto la soglia configurata, inserisce un avviso.

### `converters/txt_converter.py`
Convertitore per file di testo semplice (`.txt`). Inserisce il contenuto in un blocco preformattato Markdown (` ``` `) preservando spaziature, a capo e allineamenti. Calcola dinamicamente il numero di backtick necessari per evitare la chiusura prematura del blocco.

### `converters/web_converter.py`
Convertitore per file web (`.html`, `.htm`, `.css`, `.js`, `.ts`, `.tsx`). Funzionalità:
- **Minificazione leggera**: rimozione di commenti (`<!-- -->` per HTML, `/* */` per CSS, `//` e `/* */` per JS/TS) rispettando stringhe e template literal.
- Compressione di righe vuote multiple.
- Output in blocco di codice Markdown con tag del linguaggio appropriato.

### `converters/xlsx_converter.py`
Convertitore Excel → Markdown. Utilizza **openpyxl** con caricamento duale (formule e valori). Funzionalità:
- **Foglio per foglio**: tabella `Cella | Tipo | Formula | Valore` con entrambe le istanze (formule originali e valori calcolati).
- **Filtri**: rilevamento AutoFilter attivi.
- **Pivot Table**: rilevamento e rendering di metadati (campi riga/colonna/pagina/valore, posizione, cache) + estrazione XML grezzo da `xl/pivotTables/`.
- **Grafici**: rilevamento e rendering di metadati (tipo, titolo, assi, legenda, serie, intervalli dati, posizione).
- **Mappatura tipi grafico**: 50+ tipi di grafico in italiano.
- **Soglia XML**: troncamento configurabile dell'XML grezzo delle pivot table.

### `prompts/prompt_griglia.md`
Prompt per la **generazione automatica** della griglia di valutazione a partire dalla traccia del compito. Istruisce il LLM a produrre 3-5 criteri con punteggi che sommano a 100, livelli di prestazione (Eccellente/Buono/Sufficiente/Insufficiente) e descrizioni osservabili.

### `prompts/prompt_griglia_custom.md`
Prompt per la **normalizzazione** di una griglia fornita dal docente. Preserva criteri, nomi e punteggi del docente, convertendoli nel formato standard richiesto dal sistema. Segnala se la somma non è 100 senza correggerla.

### `prompts/prompt_correzione_senza_allegati.md`
Prompt per la correzione quando **non ci sono allegati** nella traccia. Il LLM valuta l'elaborato dello studente direttamente contro la griglia e la traccia. Include scala di ancoraggio fissa (Eccellente = 100%, Buono = 80%, Sufficiente = 60%, Insufficiente = 30%).

### `prompts/prompt_correzione_con_allegati.md`
Prompt generico per la correzione con **allegati di tipo non specializzato**. Include istruzioni per distinguere il lavoro dello studente dal template di partenza, con segnalazioni obbligate per elaborati che non derivano dall'allegato o che coincidono con esso.

### `prompts/prompt_correzione_con_allegati_docx.md`
Prompt specializzato per la correzione quando gli allegati della traccia sono documenti **`.docx`**. Include istruzioni specifiche per valutare: copertina, stili di intestazione, intestazioni/piè di pagina, sommario automatico, paragrafi di contenuto, interruzioni di pagina. Distingue il lavoro dello studente dai paragrafi vuoti del template.

### `prompts/prompt_correzione_con_allegati_xlsx.md`
Prompt specializzato per la correzione quando gli allegati della traccia sono fogli di calcolo **`.xlsx`**. Include istruzioni per valutare: formule inserite, colonne aggiunte, nuovi fogli, grafici, riferimenti assoluti, riepiloghi. Distingue celle del template invariate da celle aggiunte/modificate dallo studente.

### `prompts/prompt_riepilogo.md`
Prompt per la **generazione del riepilogo finale**. Analizza tutte le correzioni individuali e produce: statistiche generali (numero studenti, media, max, min, range), tabella punteggi individuali ordinati, osservazioni sui punti forti comuni e difficoltà ricorrenti.

### `utils/__init__.py`
Modulo di inizializzazione del pacchetto `utils`. Esporta tutte le funzioni del client LLM: `create_client`, `call_llm`, `health_check`, `call_llm_with_system_prompt`.

### `utils/llm_client.py`
Wrapper per l'interazione con il LLM via API OpenAI-compatible. Funzionalità:
- `create_client()`: istanzia il client `openai.OpenAI` con configurazione da `Settings`.
- `call_llm(messages, model)`: invia messaggi al LLM e restituisce il testo della risposta, con gestione errori di connessione e timeout.
- `call_llm_with_system_prompt(system_prompt, user_prompt, model)`: helper per chiamate con system prompt separato.
- `health_check()`: verifica se il LLM è raggiungibile elencando i modelli disponibili.

### `main.py`
Orchestratore principale di AiGrader. Contiene:
- **Parsing argomenti**: supporto per compito singolo (`python main.py "percorso"`) e modalità batch (`--batch`), con flag `--clear` per ricominciare da zero.
- **Verifica dipendenze**: controlla openpyxl, openai, aspose-words e segnala eventuali moduli mancanti.
- **Gestione nomi studente**: normalizzazione Unicode, rimozione accenti, raggruppamento di varianti dello stesso nome (es. "Mario Rossi" / "Rossi Mario") tramite matching suinsieme con transitività BFS.
- **Pipeline a 4 fasi**:
  1. **Griglia**: genera o normalizza la griglia di valutazione tramite LLM.
  2. **Consegne**: converte tutti i file degli studenti in Markdown, sceglie il prompt di correzione appropriato (in base agli allegati della traccia e ai file consegnati), e invoca il LLM per ogni studente.
  3. **Riepilogo**: genera il report aggregato con statistiche e osservazioni.
  4. **Output**: salva correzioni individuali in `correzioni/`, griglia in `griglia_valutazione.md`, riepilogo in `riepilogo.md`.
- **Gestione allegati**: estrae riferimenti da `traccia.md` (sintassi `[[allegato: nome]]`), trova file allegati, li converte in Markdown e li salva in `tmp/` per debug.
- **Report log**: registro in `elaborazioni/report.log` per tracciare i compiti già elaborati in modalità batch.
- **Pulizia**: funzione `_forced_clear()` per rimuovere correzioni precedenti e file temporanei.

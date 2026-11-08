# Directory `prompts/`

Questa directory contiene tutti i prompt testuali usati da AiGrader. I prompt
sono file Markdown con placeholder `{segnaposto}` che il codice sostituisce
prima di inviarli al modello linguistico. Modificando questi file si modifica
il comportamento del programma senza toccare il codice.

## File presenti

### Generazione / normalizzazione della griglia di valutazione

| File | Quando viene usato | Placeholder |
|---|---|---|
| `prompt_griglia.md` | Generazione automatica della griglia a partire dalla traccia (quando non esiste `griglia.md` del docente). | `{traccia}` |
| `prompt_griglia_custom.md` | Normalizzazione della griglia fornita dal docente (`griglia.md`): non aggiunge, non rimuove, non modifica criteri e punteggi. | `{traccia}`, `{griglia}` |

### Correzione degli elaborati

| File | Quando viene usato | Placeholder |
|---|---|---|
| `prompt_correzione_senza_allegati.md` | La traccia non prevede allegati. | `{nome_studente}`, `{traccia}`, `{griglia}`, `{dati_studente}` |
| `prompt_correzione_con_allegati.md` | La traccia prevede uno o più allegati di tipo misto, oppure di un tipo per cui non esiste un prompt specializzato. | `{nome_studente}`, `{traccia}`, `{allegato_originale}`, `{griglia}`, `{dati_studente}` |
| `prompt_correzione_con_allegati_xlsx.md` | Tutti gli allegati della traccia sono fogli di calcolo (`.xlsx`). | `{nome_studente}`, `{traccia}`, `{allegato_originale}`, `{griglia}`, `{dati_studente}` |
| `prompt_correzione_con_allegati_docx.md` | Tutti gli allegati della traccia sono documenti di testo (`.docx`, `.doc`). | `{nome_studente}`, `{traccia}`, `{allegato_originale}`, `{griglia}`, `{dati_studente}` |

### Riepilogo finale

| File | Quando viene usato | Placeholder |
|---|---|---|
| `prompt_riepilogo.md` | Generazione del riepilogo delle valutazioni di tutti gli studenti di un compito. | `{tutte_le_correzioni}` |

## Regola di selezione del prompt di correzione

La scelta del prompt di correzione è deterministica e dipende dagli allegati
presenti nella traccia del compito in elaborazione:

1. **Nessun allegato** → `prompt_correzione_senza_allegati.md`.
2. **Tutti gli allegati hanno la stessa estensione** `.<ext>` (es. tutti
   `.xlsx`, tutti `.docx`, tutti `.txt`) → il sistema cerca
   `prompt_correzione_con_allegati_<ext>.md`.
   - Se il file esiste, viene usato.
   - Se il file non esiste, si ricade sul prompt generico
     `prompt_correzione_con_allegati.md`.
3. **Allegati di estensioni diverse** (misto) → `prompt_correzione_con_allegati.md`.

Il nome dell'estensione è normalizzato in minuscolo e senza punto
(`.xlsx` → `xlsx`, `.docx` → `docx`).

## Come estendere il sistema con un nuovo tipo di allegato

Per aggiungere un prompt specializzato per un tipo di allegato non ancora
coperto (es. presentazioni `.pptx`):

1. Crea `prompt_correzione_con_allegati_pptx.md` in questa directory.
2. Usa gli stessi placeholder degli altri prompt di correzione con allegati:
   `{nome_studente}`, `{traccia}`, `{allegato_originale}`, `{griglia}`,
   `{dati_studente}`.
3. Adatta la sezione "Come distinguere il lavoro dello studente dal template"
   alle caratteristiche specifiche del formato (es. slide, layout, testi,
   immagini).
4. Non serve modificare il codice: la regola di selezione riconosce
   automaticamente il nuovo file quando tutti gli allegati della traccia
   hanno estensione `.pptx`.

Lo stesso vale per qualsiasi altra estensione: il nome del file prompt deve
seguire lo schema `prompt_correzione_con_allegati_<ext>.md`, dove `<ext>` è
l'estensione in minuscolo senza punto.

## Come personalizzare i prompt esistenti

Tutti i prompt sono pensati per essere modificati dal docente o dallo
sviluppatore. In particolare:

- **Griglia**: modificando `prompt_griglia.md` si cambia il modo in cui la
  griglia viene generata automaticamente dalla traccia (numero di criteri,
  descrizione dei livelli, tono). Modificando `prompt_griglia_custom.md` si
  cambia il modo in cui viene normalizzata la griglia del docente (formato
  dei livelli, gestione di note aggiuntive).
- **Correzione**: modificando i quattro prompt di correzione si cambia il
  comportamento del valutatore (scala di ancoraggio, obbligo di citazione,
  cosa non valutare, tono). Attenzione: la scala di ancoraggio
  (Eccellente 100%, Buono 80%, Sufficiente 60%, Insufficiente 30% del
  punteggio massimo) è la stessa nei quattro prompt; se la modifichi,
  fallo in tutti per coerenza.
- **Riepilogo**: modificando `prompt_riepilogo.md` si cambiano le statistiche
  riportate, il formato della tabella individuale e le osservazioni generali.

## Placeholder disponibili

| Placeholder | Contenuto | Dove è disponibile |
|---|---|---|
| `{nome_studente}` | Nome canonico dello studente. | Prompt di correzione |
| `{traccia}` | Testo della traccia del compito (senza allegati). | `prompt_griglia.md`, `prompt_griglia_custom.md`, prompt di correzione |
| `{griglia}` | Griglia di valutazione (generata o normalizzata). | `prompt_griglia_custom.md`, prompt di correzione |
| `{allegato_originale}` | Contenuto markdown degli allegati della traccia, preceduto dal nome del file. Se non ci sono allegati, il placeholder è sostituito con un testo esplicito. | Prompt di correzione con allegati |
| `{dati_studente}` | Elaborato dello studente convertito in markdown. | Prompt di correzione |
| `{tutte_le_correzioni}` | Concatenazione delle valutazioni individuali di tutti gli studenti. | `prompt_riepilogo.md` |

I placeholder non presenti in un prompt vengono comunque sostituiti dal
codice (con il valore corrispondente o con un testo di fallback): aggiungerli
o rimuoverli da un prompt non richiede modifiche al codice.

## Note tecniche

- I file prompt sono codificati in UTF-8.
- Il sistema carica il prompt al momento dell'uso: modifiche ai file hanno
  effetto alla successiva esecuzione, senza ricompilare nulla.
- La selezione del prompt di correzione è deterministica e non coinvolge il
  modello: si basa solo sul numero e sul tipo di allegati della traccia.
- Se un allegato non è convertibile in markdown, il placeholder
  `{allegato_originale}` contiene comunque una voce per quell'allegato con
  l'indicazione dell'errore, così il modello sa che esisteva ma non è stato
  possibile leggerlo.
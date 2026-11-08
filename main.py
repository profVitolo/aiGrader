"""AiGrader - Orchestratore principale per la correzione automatica di compiti.

Supporta due modalità:
- singolo (default): elabora un compito specifico dato un percorso relativo alla CWD
  (es. elaborazioni/4RIM_test_20_06_2026).
- batch: scansiona la cartella elaborazioni/ per sottocartelle compito e le
  elabora una per una.

Uso:
    python main.py "elaborazioni/4RIM_test"  # modalità singolo compito (default)
    python main.py --batch                   # modalità batch
    python main.py                           # mostra errore con usage hints
    python main.py --help                    # mostra aiuto
"""

import argparse
import logging
import re
import sys
import unicodedata
from pathlib import Path

from config.settings import Settings
from converters import get_converter, get_extension, is_supported
from utils.llm_client import call_llm

# --- Logger per warning di conversione ---
logger = logging.getLogger(__name__)

# --- Costanti ---
# PROMPT_DIR è ancorata alla CWD di lancio (dove si trovano prompts/, traccia.md, ecc.)
# e NON cambia né in modalità singolo né in modalità batch.
PROMPT_DIR = Path("prompts").resolve()

# Queste invece cambiano in base al compito in elaborazione.
BASE_DIR = Path(".")
CONSEGNE_DIR = BASE_DIR / "consegne"
CORREZIONI_DIR = BASE_DIR / "correzioni"
ELABORAZIONI_DIR = Path("elaborazioni")
REPORT_LOG = ELABORAZIONI_DIR / "report.log"  # Registro delle elaborazioni completate
TMP_DIR = None  # Cartella tmp per allegati e markdown convertiti (inizializzata per compito)

# Numero minimo di parole perché un nome sia "affidabile" per il matching
# (evita di unire "Rossi" con "Rossi Mario")
MIN_PAROLE_NOME = 2
# Differenza massima di parole ammessa per il match "sottoinsieme"
MAX_DIFF_PAROLE = 1


# --- Funzioni di supporto ---

def _scegli_prompt_correzione(allegati_traccia: dict[str, str], file_studente: list[Path],) -> str:
    """Sceglie il nome del file prompt di correzione per uno studente.

    Modello ibrido:
    1. Nessun allegato in traccia → 'prompt_correzione_senza_allegati.md'
       (il tipo di file dello studente è irrilevante: non c'è template da
       cui distinguere il lavoro).
    2. Allegati in traccia tutti della stessa estensione <ext>:
       - se esiste 'prompt_correzione_con_allegati_<ext>.md', usa quello;
       - altrimenti ricade sul generico.
       Il tipo di file dello studente è irrilevante: la traccia chiedeva
       quel tipo di lavoro, il prompt specializzato è il migliore per
       valutarlo anche se lo studente ha consegnato un formato diverso.
    3. Allegati in traccia misti:
       - se lo studente ha consegnato file di un solo tipo <ext> e esiste
         'prompt_correzione_con_allegati_<ext>.md', usa quello (più preciso
         del generico);
       - altrimenti usa il generico 'prompt_correzione_con_allegati.md'.

    Args:
        allegati_traccia: dict {nome_allegato: markdown_allegato}.
        file_studente: lista di Path dei file consegnati dallo studente
                       (con estensione originale).

    Returns:
        Nome del file prompt da caricare.
    """
    generico = "prompt_correzione_con_allegati.md"

    # Regola 1: nessun allegato in traccia
    if not allegati_traccia:
        return "prompt_correzione_senza_allegati.md"

    # Estensioni degli allegati della traccia (normalizzate)
    est_traccia = {Path(n).suffix.lower().lstrip(".") for n in allegati_traccia}
    est_traccia.discard("")

    # Regola 2: traccia con un solo tipo → prompt fisso per tutti gli studenti
    if len(est_traccia) == 1:
        ext = next(iter(est_traccia))
        candidato = f"prompt_correzione_con_allegati_{ext}.md"
        if (PROMPT_DIR / candidato).exists():
            return candidato
        logger.info(
            "Prompt specializzato '%s' non trovato per traccia con estensione "
            "unica '%s': uso il generico.",
            candidato, ext,
        )
        return generico

    # Regola 3: traccia mista → specializza per studente se possibile
    est_studente = {p.suffix.lower().lstrip(".") for p in file_studente}
    est_studente.discard("")

    if len(est_studente) == 1:
        ext = next(iter(est_studente))
        candidato = f"prompt_correzione_con_allegati_{ext}.md"
        if (PROMPT_DIR / candidato).exists():
            return candidato
        logger.info(
            "Traccia mista, studente con estensione unica '%s' ma prompt "
            "'%s' non trovato: uso il generico.",
            ext, candidato,
        )
        return generico

    # Traccia mista, studente con estensioni miste (o nessuna estensione)
    return generico
    

def _flushed_print(msg):
    print(msg, flush=True)
    

def _estrai_punteggi_massimi(griglia_md: str) -> list[int]:
    """Estrae i punteggi massimi 'X/100' dalle righe 'Punteggio massimo' della griglia.

    Cerca pattern come:
      - **Punteggio massimo**: 25/100
      - **Punteggio massimo**: 25
    """
    punteggi: list[int] = []
    pattern = re.compile(
        r"punteggio\s+massimo\**\s*[:\-]?\s*(\d+)\s*(?:/\s*100)?",
        re.IGNORECASE,
    )
    for m in pattern.finditer(griglia_md):
        punteggi.append(int(m.group(1)))
    return punteggi
    

def _valida_e_scrivi_griglia(griglia_md: str, output_path: Path) -> None:
    """Scrive la griglia e verifica che i punteggi massimi sommino a 100.

    Se la somma ≠ 100, appende una riga di warning al file (non blocca:
    la griglia è autoritativa e il docente potrebbe averla voluta così).
    """
    punteggi = _estrai_punteggi_massimi(griglia_md)
    output_path.write_text(griglia_md, encoding="utf-8")

    if not punteggi:
        logger.warning(
            "Impossibile estrarre i punteggi massimi dalla griglia: "
            "nessun pattern 'Punteggio massimo: X/100' trovato."
        )
        return

    somma = sum(punteggi)
    if somma != 100:
        logger.warning(
            "La griglia somma %d, non 100 (punteggi: %s). "
            "Aggiungo warning in coda al file.",
            somma, punteggi,
        )
        with output_path.open("a", encoding="utf-8") as f:
            f.write(
                f"\n\n> ⚠️ **ATTENZIONE**: la somma dei punteggi massimi "
                f"della griglia è {somma}, non 100 "
                f"(punteggi rilevati: {', '.join(map(str, punteggi))}). "
                f"Verificare la griglia fornita.\n"
            )
    else:
        logger.info("Griglia valida: somma punteggi = 100 (%s).", punteggi)


def _verifica_dipendenze() -> list[tuple[str, str]]:
    """Verifica che i moduli Python richiesti siano installati.

    Controlla la disponibilità di: openpyxl, openai, aspose.words.
    Stampa un warning con le istruzioni di installazione per ogni modulo
    mancante e restituisce la lista dei mancanti.

    Returns:
        Lista di tuple (pacchetto, descrizione) dei moduli mancanti.
        Lista vuota se tutte le dipendenze sono soddisfatte.
    """
    dipendenze = {
        "openpyxl": ("openpyxl", ".xlsx"),
        "openai": ("openai", "interazione LLM"),
        "aspose.words": ("aspose-words", ".doc"),
    }

    mancanti: list[tuple[str, str]] = []
    for modulo, (pacchetto, descrizione) in dipendenze.items():
        try:
            __import__(modulo)
        except ImportError:
            mancanti.append((pacchetto, descrizione))

    if mancanti:
        _flushed_print("\n" + "=" * 60)
        _flushed_print("ATTENZIONE: Dipendenze mancanti rilevate")
        _flushed_print("=" * 60)
        _flushed_print("Installa i moduli mancanti con:\n")
        for pacchetto, descrizione in mancanti:
            _flushed_print(f"  - {pacchetto}: pip install {pacchetto}  (per {descrizione})")
        _flushed_print("")

    return mancanti


def _forced_clear() -> None:
    """Pulisce gli correzioni di una precedente elaborazione nel compito corrente.

    Rimuove, se presenti, nella cartella BASE_DIR:
    - contenuto della cartella correzioni/
    - contenuto della cartella tmp/
    - file griglia_valutazione.md
    - file riepilogo.md

    Non tocca: consegne/, griglia.md (del docente), traccia.md, e i file di
    configurazione. Non tocca report.log (che è globale, in elaborazioni/).
    """
    _flushed_print("  -> --clear: pulizia correzioni precedenti...")

    # correzioni/
    if CORREZIONI_DIR.exists():
        for item in CORREZIONI_DIR.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except OSError as e:
                logger.warning("Impossibile rimuovere %s: %s", item, e)

    # tmp/
    tmp_dir = BASE_DIR / "tmp"
    if tmp_dir.exists():
        for item in tmp_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except OSError as e:
                logger.warning("Impossibile rimuovere %s: %s", item, e)

    # griglia_valutazione.md e riepilogo.md
    for nome_file in ("griglia_valutazione.md", "riepilogo.md"):
        percorso = BASE_DIR / nome_file
        if percorso.exists():
            try:
                percorso.unlink()
            except OSError as e:
                logger.warning("Impossibile rimuovere %s: %s", percorso, e)

    _flushed_print("  -> Pulizia completata.")


def _carica_elaborazioni_completate() -> set[str]:
    """Legge report.log e restituisce un set con i nomi delle cartelle già valutate."""
    completate = set()
    if not REPORT_LOG.exists():
        return completate

    with REPORT_LOG.open("r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            completate.add(linea)
    return completate


def _registra_elaborazione(nome_cartella: str) -> None:
    """Appende il nome della cartella a report.log dopo una valutazione completata."""
    REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{nome_cartella}\n")


# --- Estrazione e raggruppamento nomi studente ---

def _estrai_nome_studente(nome_file: str) -> str:
    """Estrae il nome dello studente dal nome del file.

    Supporta formati come:
    - "rossi mario - bilancio" → "Rossi Mario"
    - "bianchi luca.docx" → "Bianchi Luca"

    Se il file non segue il formato atteso (senza '-'), usa lo stem come nome.

    NOTA: questa funzione è mantenuta per compatibilità e usi puntuali.
    Il raggruppamento per studente usa _normalizza_nome + _raggruppa_per_studente,
    che gestiscono in modo robusto anche l'inversione nome/cognome.
    """
    stem = Path(nome_file).stem
    match = re.match(r"^(.*?)\s*-\s*", stem)
    if match:
        nome = match.group(1).strip()
        if not nome or nome in ('-', ''):
            logger.warning(
                "Nome studente vuoto o non valido per file '%s': uso lo stem come fallback",
                nome_file,
            )
            stem_pulito = stem.lstrip('- ').strip()
            if stem_pulito:
                return stem_pulito.title()
            return stem.title()
        return nome.title()
    return stem.title()


def _normalizza_nome(nome: str) -> frozenset[str]:
    """Normalizza un nome in un insieme di parole minuscole senza accenti.

    Esempi:
        "Mario Rossi"       → frozenset({"mario", "rossi"})
        "ROSSI Mario"       → frozenset({"mario", "rossi"})
        "De Angelis Marco"  → frozenset({"de", "angelis", "marco"})
        "Nicolò D'Angelo"   → frozenset({"angelo", "d", "nicolo"})
    """
    nome = unicodedata.normalize("NFKD", nome)
    nome = "".join(c for c in nome if not unicodedata.combining(c))
    parole = re.findall(r"[a-z]+", nome.lower())
    return frozenset(parole)


def _stesso_studente(a: frozenset[str], b: frozenset[str]) -> bool:
    """Determina se due insiemi di parole rappresentano lo stesso studente.

    Regola:
    - Se gli insiemi sono uguali → stesso studente (es. "Mario Rossi" / "Rossi Mario").
    - Se uno è sottoinsieme dell'altro e la differenza è al massimo
      MAX_DIFF_PAROLE → stesso studente, a condizione che il più piccolo
      abbia almeno MIN_PAROLE_NOME parole.
      (es. "Mario Rossi" / "Mario Rossi Giuseppe" → stesso studente)

    In tutti gli altri casi → studenti diversi.
    """
    if a == b:
        return True

    piccolo, grande = (a, b) if len(a) <= len(b) else (b, a)

    if len(piccolo) < MIN_PAROLE_NOME:
        return False

    if not piccolo.issubset(grande):
        return False

    return (len(grande) - len(piccolo)) <= MAX_DIFF_PAROLE


def _raggruppa_per_studente(nomi_originali: list[str]) -> list[list[str]]:
    """Raggruppa una lista di nomi (grezzi) in cluster di "stesso studente".

    Usa _stesso_studente per costruire un grafo implicito nome→nome e
    restituisce le componenti connesse (BFS). Questo gestisce la transitività:
    se A~B e B~C, allora A, B e C finiscono nello stesso gruppo.

    Args:
        nomi_originali: lista di nomi come compaiono nei file (es. "ROSSI Mario").

    Returns:
        Lista di gruppi; ogni gruppo è una lista di nomi originali.
    """
    normalizzati: dict[str, frozenset[str]] = {
        n: _normalizza_nome(n) for n in nomi_originali
    }

    visitati: set[str] = set()
    gruppi: list[list[str]] = []

    for nome in nomi_originali:
        if nome in visitati:
            continue

        gruppo: list[str] = [nome]
        visitati.add(nome)
        coda: list[str] = [nome]

        while coda:
            corrente = coda.pop()
            for altro in nomi_originali:
                if altro in visitati:
                    continue
                if _stesso_studente(normalizzati[corrente], normalizzati[altro]):
                    gruppo.append(altro)
                    visitati.add(altro)
                    coda.append(altro)

        gruppi.append(gruppo)

    return gruppi


def _nome_canonico(gruppo: list[str]) -> str:
    """Sceglie il nome canonico di un gruppo di nomi equivalenti.

    Criterio: più parole vince; a parità, nome più lungo; a parità, primo
    incontrato. Il risultato è in Title Case.
    """
    def chiave(nome: str):
        parole = _normalizza_nome(nome)
        return (len(parole), len(nome))

    migliore = max(gruppo, key=chiave)
    return migliore.title()


def _raggruppa_file_per_studente(file_paths: list[Path]) -> dict[str, list[Path]]:
    """Raggruppa i file per studente gestendo inversioni nome/cognome.

    Per ogni file estrae il nome dello studente (parte prima del ' - '),
    normalizza il nome in un insieme di parole e unisce i file che
    rappresentano lo stesso studente (vedi _stesso_studente).

    Il nome restituito come chiave è il "nome canonico" del gruppo
    (vedi _nome_canonico), non necessariamente quello del primo file.

    Args:
        file_paths: Lista di Path dei file degli studenti.

    Returns:
        Dict che mappa il nome canonico dello studente alla lista dei suoi file.
    """
    # Estrai il nome "grezzo" da ciascun file
    grezzi_per_file: list[tuple[Path, str]] = []
    for fp in file_paths:
        nome = _estrai_nome_studente(fp.name)
        grezzi_per_file.append((fp, nome))

    nomi_unici = sorted({n for _, n in grezzi_per_file})
    gruppi = _raggruppa_per_studente(nomi_unici)

    # Mappa nome_grezzo → nome_canonico del suo gruppo
    mappa_nomi: dict[str, str] = {}
    for gruppo in gruppi:
        canonico = _nome_canonico(gruppo)
        for n in gruppo:
            mappa_nomi[n] = canonico

        # Log: se il gruppo contiene nomi non identici dopo normalizzazione
        normalizzati_gruppo = {n: _normalizza_nome(n) for n in gruppo}
        if len(set(normalizzati_gruppo.values())) > 1:
            logger.warning(
                "Raggruppati nomi considerati equivalenti: %s → canonico '%s'",
                " | ".join(sorted(gruppo)),
                canonico,
            )

    # Costruisci il risultato
    raggruppato: dict[str, list[Path]] = {}
    for fp, nome in grezzi_per_file:
        canonico = mappa_nomi[nome]
        raggruppato.setdefault(canonico, []).append(fp)

    return raggruppato


# --- Elaborazione cartelle e file ---

def _processa_studente_folder(folder_path: Path) -> tuple[str, str]:
    """Processa una cartella studente in modo ricorsivo.

    Scansiona tutti i file nella cartella (e sottocartelle), converte ciascun
    file supportato, e restituisce un markdown unificato. Il nome studente
    è estratto dal nome della cartella e normalizzato.

    Args:
        folder_path: Percorso della cartella dello studente.

    Returns:
        Tuple (nome_studente, markdown_contenuto).

    Raises:
        ValueError: Se la cartella non esiste, è vuota o non contiene file
                    supportati.
    """
    if not folder_path.exists():
        raise ValueError(f"Cartella non trovata: {folder_path}")

    if not folder_path.is_dir():
        raise ValueError(f"Percorso non è una cartella: {folder_path}")

    # Nome canonico: title case, con normalizzazione minima (lascia che sia
    # _nome_canonico a fare il lavoro "vero" — qui c'è un solo nome, quindi
    # il risultato è deterministico)
    nome_studente = _nome_canonico([folder_path.name])

    file_paths = sorted(
        [f for f in folder_path.rglob("*") if f.is_file()],
        key=lambda p: str(p.relative_to(folder_path)).lower(),
    )

    # Escludi file nascosti e temporanei Office
    file_paths = [
        f for f in file_paths
        if not f.name.startswith(".")
        and not f.name.startswith("~$")
    ]

    if not file_paths:
        raise ValueError(f"Cartella vuota: {folder_path}")

    file_supportati = [f for f in file_paths if is_supported(f)]

    nomi_file_visti: dict[str, list[Path]] = {}
    for f in file_supportati:
        nomi_file_visti.setdefault(f.name, []).append(f)

    for nome, paths in nomi_file_visti.items():
        if len(paths) > 1:
            logger.warning(
                "File duplicati rilevati in %s: %s",
                folder_path.name,
                ", ".join(str(p.relative_to(folder_path)) for p in paths),
            )

    if not file_supportati:
        raise ValueError(
            f"Nessun file supportato trovato in {folder_path}. "
            f"Estensioni supportate: {', '.join(Settings.CONVERTERS_MAP.keys())}"
        )

    parti: list[str] = [f"# Consegna di {nome_studente}\n"]
    nomi_file_usati: set[str] = set()

    for file_path in file_supportati:
        percorso_rel = file_path.relative_to(folder_path)
        nome_file_display = str(percorso_rel)

        if nome_file_display in nomi_file_usati:
            # Rende univoco il display aggiungendo un contatore
            contatore = 2
            base = nome_file_display
            while nome_file_display in nomi_file_usati:
                nome_file_display = f"{base} (copia {contatore})"
                contatore += 1
            logger.info(
                "File duplicato rilevato, display univoco: %s",
                nome_file_display,
            )
        nomi_file_usati.add(nome_file_display)

        parte_markdown = f"## File: {nome_file_display}\n\n"

        funzione_conversione = get_converter(get_extension(file_path))
        if not funzione_conversione:
            parte_markdown += (
                f"[Errore nella conversione: Nessun convertitore disponibile "
                f"per {file_path.suffix}]\n"
            )
            parti.append(parte_markdown)
            continue

        try:
            contenuto = funzione_conversione(file_path)
            if contenuto:
                parte_markdown += contenuto
            else:
                parte_markdown += "[ATTENZIONE] Nessun contenuto estratto dal file\n"
        except Exception as e:
            parte_markdown += f"[Errore nella conversione: {e}]\n"

        parti.append(parte_markdown)

    return nome_studente, "\n".join(parti)


def _processa_studente_multi_file(nome_studente: str, file_paths: list[Path],) -> tuple[str, list[Path]]:
    """Processa più file per uno stesso studente e restituisce un markdown unificato.

    Args:
        nome_studente: Nome canonico dello studente (es. 'Mario Rossi').
        file_paths: Lista di Path dei file da convertire.

    Returns:
        Tupla (markdown_unificato, file_paths_ordinati), dove file_paths_ordinati
        è la lista dei Path originali nell'ordine di elaborazione (usata per la
        scelta del prompt di correzione).
    """
    file_paths_ordinate = sorted(file_paths, key=lambda p: p.name.lower())

    parti: list[str] = [f"# Consegna di {nome_studente}\n"]
    nomi_file_usati: set[str] = set()

    for file_path in file_paths_ordinate:
        nome_file_display = file_path.name

        if nome_file_display in nomi_file_usati:
            contatore = 2
            base = nome_file_display
            while nome_file_display in nomi_file_usati:
                nome_file_display = f"{base} (copia {contatore})"
                contatore += 1
            logger.warning(
                "File duplicato rilevato per %s: uso display univoco '%s'",
                nome_studente,
                nome_file_display,
            )
        nomi_file_usati.add(nome_file_display)

        parte_markdown = f"## File: {nome_file_display}\n\n"

        funzione_conversione = get_converter(get_extension(file_path))
        if not funzione_conversione:
            parte_markdown += (
                f"[Errore nella conversione: Nessun convertitore disponibile "
                f"per {file_path.suffix}]\n"
            )
            parti.append(parte_markdown)
            continue

        try:
            contenuto = funzione_conversione(file_path)
            if contenuto:
                parte_markdown += contenuto
            else:
                parte_markdown += "[ATTENZIONE] Nessun contenuto estratto dal file\n"
        except Exception as e:
            parte_markdown += f"[Errore nella conversione: {e}]\n"

        parti.append(parte_markdown)

    return "\n".join(parti), file_paths_ordinate
    

# --- Prompt e allegati ---

def _carica_prompt(nome_file: str) -> str:
    """Carica un file prompt dalla working directory."""
    prompt_path = PROMPT_DIR / nome_file
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt non trovato: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _estrai_riferimenti_allegati_traccia() -> list[str]:
    """Estrae i riferimenti ad allegati dal contenuto di traccia.md.

    Cerca la sintassi [[allegato: nomefile.ext]] nel file traccia.md.
    Restituisce una lista di nomi di file (senza percorso).
    """
    traccia_path = BASE_DIR / "traccia.md"
    if not traccia_path.exists():
        return []

    contenuto = traccia_path.read_text(encoding="utf-8")
    pattern = r"\[\[allegato:\s*([^\]]+)\]\]"
    return re.findall(pattern, contenuto, re.IGNORECASE)


def _trova_allegati_traccia() -> list[Path]:
    """Trova file allegati alla traccia nella working directory.

    Cerca in due modi:
    1. File con prefisso 'traccia' e estensioni supportate (.xlsx, .docx, .doc, .html)
    2. File referenziati esplicitamente in traccia.md con [[allegato: nomefile.ext]]

    Restituisce una lista ordinata e deduplicata di Path.
    """
    allegati_supportati = {".xlsx", ".docx", ".doc", ".html"}
    allegati: dict[str, Path] = {}

    if not BASE_DIR.exists():
        return []

    for file_path in sorted(BASE_DIR.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.name.lower().startswith("traccia") and file_path.suffix.lower() in allegati_supportati:
            allegati[file_path.name.lower()] = file_path

    riferimenti = _estrai_riferimenti_allegati_traccia()
    for nome_file in riferimenti:
        nome_file_pulito = nome_file.strip()
        percorso_allegato = BASE_DIR / nome_file_pulito
        if percorso_allegato.is_file() and percorso_allegato.suffix.lower() in allegati_supportati:
            allegati[nome_file_pulito.lower()] = percorso_allegato

    return list(allegati.values())


def _converti_allegato(file_path: Path) -> str:
    """Converte un allegato in markdown usando il convertitore appropriato."""
    funzione_conversione = get_converter(get_extension(file_path))
    if not funzione_conversione:
        logger.warning("Nessun convertitore disponibile per %s", file_path.name)
        return ""

    try:
        return funzione_conversione(file_path)
    except Exception as e:
        logger.warning("Errore nella conversione di %s: %s", file_path.name, e)
        return ""


def _crea_traccia_combinata() -> tuple[str, dict[str, str]]:
    """Crea traccia.md combinato in tmp/ con traccia originale + allegati convertiti.

    Ogni allegato viene anche salvato in un file di debug separato in tmp/,
    con nome che include TUTTI i suffissi (es. 'traccia_Verifica.xlsx.md'),
    così allegati con lo stesso stem ma estensione diversa non si sovrascrivono.
    Viene inoltre generato 'tmp/allegati_index.md' con l'elenco degli allegati
    processati, esito e dimensione del markdown prodotto.

    Returns:
        Tupla (contenuto_completo, allegati_md) dove:
        - contenuto_completo: stringa markdown = traccia originale + sezioni
          '## Allegato: <nome>' con il markdown di ciascun allegato. Serve alla
          generazione/normalizzazione della griglia.
        - allegati_md: dict {nome_file_allegato: markdown_allegato} con i soli
          allegati convertiti (senza la traccia). Serve alla correzione degli
          elaborati (Fase 6.5).
    """
    traccia_originale = (BASE_DIR / "traccia.md").read_text(encoding="utf-8")
    allegati = _trova_allegati_traccia()

    if not allegati:
        combined_path = TMP_DIR / "traccia.md"
        combined_path.write_text(traccia_originale, encoding="utf-8")
        _flushed_print(f"  -> Salvata traccia.md (senza allegati) in tmp/")
        return traccia_originale, {}

    parti = [traccia_originale]
    allegati_md: dict[str, str] = {}
    righe_indice: list[str] = [
        "# Indice allegati traccia",
        "",
        "| Allegato | Tipo | Dimensione markdown | Esito | File debug |",
        "|---|---|---|---|---|",
    ]

    SOGLIA_VUOTO = 200  # caratteri

    for allegato_path in allegati:
        contenuto_markdown = _converti_allegato(allegato_path)
        suffix_completo = "".join(allegato_path.suffixes) or ".bin"
        dump_name = f"traccia_{allegato_path.stem}{suffix_completo}.md"

        if contenuto_markdown:
            parti.append(
                f"\n\n## Allegato: {allegato_path.name}\n\n{contenuto_markdown}"
            )
            allegati_md[allegato_path.name] = contenuto_markdown

            tmp_output_path = TMP_DIR / dump_name
            tmp_output_path.write_text(contenuto_markdown, encoding="utf-8")

            lunghezza = len(contenuto_markdown.strip())
            if lunghezza < SOGLIA_VUOTO:
                esito = f"⚠️ quasi vuoto ({lunghezza} char)"
                _flushed_print(
                    f"     [ATTENZIONE] Markdown molto corto per "
                    f"{allegato_path.name} ({lunghezza} char) — "
                    f"possibile template senza contenuto sostanziale"
                )
            else:
                esito = "ok"
                _flushed_print(f"     -> Salvato allegato: {dump_name}")

            righe_indice.append(
                f"| {allegato_path.name} | {suffix_completo} | "
                f"{lunghezza} char | {esito} | `{dump_name}` |"
            )
        else:
            _flushed_print(
                f"     [ATTENZIONE] Nessun contenuto estratto da "
                f"{allegato_path.name}"
            )
            righe_indice.append(
                f"| {allegato_path.name} | {suffix_completo} | "
                f"0 char | ❌ vuoto | — |"
            )

    contenuto_completo = "\n".join(parti)
    combined_path = TMP_DIR / "traccia.md"
    combined_path.write_text(contenuto_completo, encoding="utf-8")

    indice_path = TMP_DIR / "allegati_index.md"
    indice_path.write_text("\n".join(righe_indice) + "\n", encoding="utf-8")

    _flushed_print(
        f"  -> Salvata traccia.md combinata "
        f"({len(allegati)} allegato/i) in tmp/"
    )
    _flushed_print(f"  -> Salvato indice allegati: {indice_path.name}")
    return contenuto_completo, allegati_md
    

# --- Pipeline principale ---

def _elabora_griglia() -> tuple[str, str, dict[str, str]]:
    """Genera o normalizza la griglia di valutazione.

    Se nella cartella del compito esiste ``griglia.md``, viene usata come
    griglia autoritativa del docente e solo normalizzata nel formato standard
    tramite ``prompt_griglia_custom.md``. Altrimenti si ricade sulla
    generazione automatica da traccia tramite ``prompt_griglia.md``.

    La traccia combinata (traccia + allegati) viene sempre prodotta, anche
    quando la griglia è in cache, perché serve alla correzione degli elaborati.

    Returns:
        Tupla (griglia_md, traccia_pura, allegati_md) dove:
        - griglia_md: contenuto markdown della griglia di valutazione.
        - traccia_pura: testo di traccia.md SENZA allegati combinati.
        - allegati_md: dict {nome_allegato: markdown_allegato}.
    """
    global TMP_DIR

    griglia_docente_path = BASE_DIR / "griglia.md"
    griglia_output_path = BASE_DIR / "griglia_valutazione.md"

    # Traccia pura: sempre letta, serve alla correzione anche con griglia in cache
    traccia_pura = (BASE_DIR / "traccia.md").read_text(encoding="utf-8")

    # Traccia combinata + allegati: sempre prodotti (dump + correzione)
    _flushed_print("  -> Preparazione traccia combinata e allegati...")
    contenuto_traccia, allegati_md = _crea_traccia_combinata()

    # Riusa la griglia già normalizzata, se esiste
    if griglia_output_path.exists():
        _flushed_print("[1/4] Griglia esistente trovata, la riuso (cancella il file per rigenerarla).")
        return griglia_output_path.read_text(encoding="utf-8"), traccia_pura, allegati_md

    if griglia_docente_path.exists():
        _flushed_print("[1/4] Trovata griglia.md del docente: normalizzazione in corso...")

        prompt_custom_tmpl = _carica_prompt("prompt_griglia_custom.md")
        griglia_docente = griglia_docente_path.read_text(encoding="utf-8")

        griglia_contenuto = call_llm([
            {"role": "user", "content": prompt_custom_tmpl
                .replace("{traccia}", contenuto_traccia)
                .replace("{griglia}", griglia_docente)}
        ])

        _valida_e_scrivi_griglia(griglia_contenuto, griglia_output_path)
        _flushed_print("-> Griglia del docente normalizzata.\n")
        return griglia_contenuto, traccia_pura, allegati_md

    # Fallback: generazione automatica dalla traccia
    _flushed_print("[1/4] Nessuna griglia.md trovata: generazione automatica della griglia dalla traccia...")

    prompt_griglia_tmpl = _carica_prompt("prompt_griglia.md")

    griglia_contenuto = call_llm([
        {"role": "user", "content": prompt_griglia_tmpl.replace("{traccia}", contenuto_traccia)}
    ])

    _valida_e_scrivi_griglia(griglia_contenuto, griglia_output_path)
    _flushed_print("-> Griglia generata automaticamente.\n")
    return griglia_contenuto, traccia_pura, allegati_md


def _elabora_consegne(griglia: str, traccia_pura: str, allegati_md: dict[str, str],) -> str:
    """Elabora tutte le consegne degli studenti e restituisce il testo concatenato.

    Supporta due modalità:
    - Cartelle studente: directory in consegne/ elaborate con _processa_studente_folder()
    - File multipli: file in consegne/ raggruppati per studente con _raggruppa_file_per_studente()
      e poi elaborati con _processa_studente_multi_file()

    Entrambi i percorsi producono un markdown unificato per studente e la lista
    dei file originali consegnati. Il prompt di correzione viene scelto per
    ogni studente (vedi _scegli_prompt_correzione) in base agli allegati della
    traccia e ai file consegnati.

    Al termine, per ogni studente, il file di correzione contiene in coda
    una riga che riporta il prompt usato e i nomi degli allegati visti.

    Args:
        griglia: contenuto markdown della griglia di valutazione.
        traccia_pura: testo di traccia.md senza allegati combinati.
        allegati_md: dict {nome_allegato: markdown_allegato}.

    Returns:
        Concatenazione di tutte le correzioni individuali, con separatori
        '--- STUDENTE: <nome> ---' per il riepilogo.
    """
    _flushed_print("[2/4] Elaborazione delle consegne degli studenti...")

    # Blocco allegati per il placeholder {allegato_originale} (costante per compito)
    if allegati_md:
        allegati_blocco = "\n\n".join(
            f"### Allegato originale: {nome}\n\n{md}"
            for nome, md in allegati_md.items()
        )
        allegati_log = ", ".join(allegati_md.keys())
    else:
        allegati_blocco = "(nessun allegato alla traccia)"
        allegati_log = "nessuno"

    tutte_le_correzioni_text = ""
    studenti_elaborati = 0

    if not CONSEGNE_DIR.exists():
        _flushed_print(f"  [ATTENZIONE] Cartella {CONSEGNE_DIR} non trovata.")
        return ""

    # studenti_da_elaborare: nome_canonico → (markdown, origine, file_studente)
    studenti_da_elaborare: dict[str, tuple[str, str, list[Path]]] = {}

    # 1) Cartelle studente
    cartelle: list[Path] = []
    file_singoli: list[Path] = []
    for item in CONSEGNE_DIR.iterdir():
        if item.is_dir():
            cartelle.append(item)
        elif item.is_file():
            if not is_supported(item):
                _flushed_print(f"  [SKIP] File non supportato: {item.name}")
                continue
            file_singoli.append(item)

    for cartella in cartelle:
        _flushed_print(f" -> Rilevata cartella studente: {cartella.name}")
        try:
            nome, markdown, file_studente = _processa_studente_folder(cartella)
        except ValueError as e:
            _flushed_print(f"    [ATTENZIONE] Cartella saltata {cartella.name}: {e}")
            continue
        except Exception as e:
            _flushed_print(f"    [ERRORE] Errore nella cartella {cartella.name}: {e}")
            continue

        origine = f"cartella:{cartella.name}"
        if nome in studenti_da_elaborare:
            vecchio_md, vecchia_origine, vecchi_file = studenti_da_elaborare[nome]
            logger.warning(
                "Conflitto nome studente '%s': già presente da [%s], "
                "aggiungo contenuto da [%s]",
                nome,
                vecchia_origine,
                origine,
            )
            studenti_da_elaborare[nome] = (
                vecchio_md + "\n\n" + markdown,
                f"{vecchia_origine}; {origine}",
                vecchi_file + file_studente,
            )
        else:
            studenti_da_elaborare[nome] = (markdown, origine, list(file_studente))

        _flushed_print(f"    -> Cartella elaborata: {nome}")

    # 2) File singoli raggruppati per studente
    if file_singoli:
        _flushed_print(f" -> Rilevati {len(file_singoli)} file singoli da raggruppare...")
        raggruppato = _raggruppa_file_per_studente(file_singoli)

        for nome_canonico, paths in raggruppato.items():
            _flushed_print(
                f" -> Studente: {nome_canonico} "
                f"({len(paths)} file: {', '.join(p.name for p in paths)})"
            )
            try:
                markdown, file_studente = _processa_studente_multi_file(
                    nome_canonico, paths
                )
            except Exception as e:
                _flushed_print(f"    [ERRORE] Impossibile elaborare {nome_canonico}: {e}")
                continue

            origine = f"file:{', '.join(p.name for p in paths)}"
            if nome_canonico in studenti_da_elaborare:
                vecchio_md, vecchia_origine, vecchi_file = studenti_da_elaborare[nome_canonico]
                logger.warning(
                    "Conflitto nome studente '%s': già presente da [%s], "
                    "aggiungo contenuto da [%s]",
                    nome_canonico,
                    vecchia_origine,
                    origine,
                )
                studenti_da_elaborare[nome_canonico] = (
                    vecchio_md + "\n\n" + markdown,
                    f"{vecchia_origine}; {origine}",
                    vecchi_file + file_studente,
                )
            else:
                studenti_da_elaborare[nome_canonico] = (
                    markdown, origine, list(file_studente)
                )

    if not studenti_da_elaborare:
        _flushed_print(f"  [ATTENZIONE] Nessuno studente da elaborare in {CONSEGNE_DIR}.")
        return ""

    # 3) Correzione LLM per ogni studente
    for nome_studente, (markdown_contenuto, origine, file_studente) in studenti_da_elaborare.items():
        nome_file_sicuro = re.sub(r'[^\w\s-]', '', nome_studente).strip().replace(' ', '_')
        tmp_output_path = TMP_DIR / f"consegna_{nome_file_sicuro}.md"
        tmp_output_path.write_text(markdown_contenuto, encoding="utf-8")
        _flushed_print(f"    -> Salvato markdown unificato: {tmp_output_path.name}")

        # --- Scelta del prompt di correzione per questo studente ---
        nome_prompt = _scegli_prompt_correzione(allegati_md, file_studente)
        _flushed_print(f"    -> Prompt di correzione: {nome_prompt}")

        try:
            prompt_corr_tmpl = _carica_prompt(nome_prompt)

            prompt_correzione_finale = (
                prompt_corr_tmpl
                .replace("{nome_studente}", nome_studente)
                .replace("{griglia}", griglia)
                .replace("{traccia}", traccia_pura)
                .replace("{allegato_originale}", allegati_blocco)
                .replace("{dati_studente}", markdown_contenuto)
            )

            report_studente = call_llm(
                [{"role": "user", "content": prompt_correzione_finale}]
            )

            riga_log = (
                f"\n\n> Prompt di correzione usato: `{nome_prompt}` · "
                f"Allegati visti: {allegati_log}\n"
            )

            output_path = CORREZIONI_DIR / f"{nome_studente} - griglia.md"
            output_path.write_text(
                report_studente + riga_log, encoding="utf-8"
            )

            tutte_le_correzioni_text += (
                f"\n\n--- STUDENTE: {nome_studente} ---\n" + report_studente
            )
            studenti_elaborati += 1

        except Exception as e:
            _flushed_print(f"    [ERRORE] Impossibile correggere {nome_studente}: {e}")
            continue

    _flushed_print(f"  -> {studenti_elaborati} studente/i elaborati.\n")
    return tutte_le_correzioni_text


def _genera_riepilogo(tutte_le_correzioni: str) -> None:
    """Genera il riepilogo finale delle valutazioni."""
    _flushed_print("[3/4] Generazione del report di riepilogo finale...")

    if not tutte_le_correzioni.strip():
        _flushed_print("  [ATTENZIONE] Nessuna correzione da riepilogare.")
        return

    prompt_riepilogo_tmpl = _carica_prompt("prompt_riepilogo.md")

    riepilogo_contenuto = call_llm(
        [{"role": "user", "content": prompt_riepilogo_tmpl.replace(
            "{tutte_le_correzioni}", tutte_le_correzioni
        )}]
    )

    (BASE_DIR / "riepilogo.md").write_text(riepilogo_contenuto, encoding="utf-8")
    _flushed_print("[4/4] Pipeline completata con successo!\n")


def _elabora_compito_singolo(clear: bool = False) -> bool:
    """Elabora un singolo compito nella working directory.

    Args:
        clear: se True, pulisce gli correzioni di una precedente elaborazione
               (correzioni/, tmp/, griglia_valutazione.md, riepilogo.md)
               prima di procedere.

    Returns:
        True se la pipeline è completata con successo, False altrimenti.
        Non chiama sys.exit(): la decisione di uscire è lasciata al chiamante.
    """
    global TMP_DIR
    _flushed_print("=" * 60)
    _flushed_print("AiGrader - Modalità Compito Singolo")
    _flushed_print("=" * 60)

    if clear:
        _forced_clear()

    CORREZIONI_DIR.mkdir(exist_ok=True)
    TMP_DIR = BASE_DIR / "tmp"
    TMP_DIR.mkdir(exist_ok=True)

    try:
        griglia, traccia_pura, allegati_md = _elabora_griglia()
        tutte_le_correzioni = _elabora_consegne(griglia, traccia_pura, allegati_md)
        _genera_riepilogo(tutte_le_correzioni)
        return True
    except FileNotFoundError as e:
        _flushed_print(f"\n[ERRORE] {e}")
        return False
    except Exception as e:
        _flushed_print(f"\n[ERRORE] Errore durante la pipeline: {e}")
        return False


def _elabora_batch(clear: bool = False) -> None:
    """Elabora tutti i compiti nella cartella elaborazioni/.

    Args:
        clear: se True, rielabora anche i compiti già registrati in report.log
               e pulisce gli correzioni di ogni compito prima di procedere.
    """
    global BASE_DIR, CONSEGNE_DIR, CORREZIONI_DIR, TMP_DIR

    _flushed_print("=" * 60)
    _flushed_print("AiGrader - Modalità Batch")
    _flushed_print("=" * 60)

    if not ELABORAZIONI_DIR.exists():
        _flushed_print(f"[ERRORE] Cartella {ELABORAZIONI_DIR} non trovata.")
        sys.exit(1)

    completate = set() if clear else _carica_elaborazioni_completate()

    task_dirs = [d for d in ELABORAZIONI_DIR.iterdir() if d.is_dir()]
    if not task_dirs:
        _flushed_print(f"[ATTENZIONE] Nessuna sottocartella trovata in {ELABORAZIONI_DIR}.")
        sys.exit(0)

    da_elaborare = [d for d in task_dirs if d.name not in completate]
    saltate = len(task_dirs) - len(da_elaborare)

    if saltate:
        _flushed_print(f"Saltate {saltate} elaborazione/i già completate (report.log).\n")

    if not da_elaborare:
        _flushed_print("[INFO] Tutti i compiti sono già stati elaborati.")
        sys.exit(0)

    _flushed_print(
        f"Trovati {len(task_dirs)} compito/i totali, "
        f"{len(da_elaborare)} da elaborare.\n"
    )

    elaborati = 0
    falliti = 0
    for task_dir in da_elaborare:
        _flushed_print(f"\n{'#' * 60}")
        _flushed_print(f"# Compito: {task_dir.name}")
        _flushed_print(f"{'#' * 60}")

        BASE_DIR = task_dir
        CONSEGNE_DIR = BASE_DIR / "consegne"
        CORREZIONI_DIR = BASE_DIR / "correzioni"

        try:
            successo = _elabora_compito_singolo(clear=clear)
            if successo:
                if clear and task_dir.name in _carica_elaborazioni_completate():
                    # Già registrato: non duplicare
                    pass
                else:
                    _registra_elaborazione(task_dir.name)
                elaborati += 1
            else:
                falliti += 1
                _flushed_print(
                    f"[ATTENZIONE] Compito {task_dir.name} NON registrato "
                    f"in report.log (pipeline fallita)."
                )
        except KeyboardInterrupt:
            _flushed_print("\n[INTERROTTO] Batch interrotto dall'utente.")
            break
        except Exception as e:
            falliti += 1
            _flushed_print(f"[ERRORE] Compito {task_dir.name} fallito: {e}")
        finally:
            TMP_DIR = None

    _flushed_print(f"\n{'=' * 60}")
    _flushed_print(f"Batch completato: {elaborati} elaborati, {falliti} falliti.")
    _flushed_print(f"{'=' * 60}")


# --- Argparse ---

def _parse_args() -> argparse.Namespace:
    """Parsa i parametri da riga di comando."""
    parser = argparse.ArgumentParser(
        description="AiGrader - Correzione automatica di compiti scolastici.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  python main.py "elaborazioni/4RIM - Esercitazione di Google Fogli"  # Compito specifico
  python main.py --batch                                              # Batch di tutti i compiti
        """,
    )

    parser.add_argument(
        "cartella_compito",
        type=str,
        nargs="?",
        metavar="PERCORSO_CARTELLA",
        help="Percorso della cartella del compito da elaborare (modalità singolo, default)",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Elabora tutti i compiti nella cartella elaborazioni/",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Rilancia l'elaborazione anche se già completata, "
             "pulendo correzioni/, tmp/, griglia_valutazione.md e riepilogo.md.",
    )
    
    return parser.parse_args()


def _show_usage_error_and_exit() -> None:
    """Mostra un messaggio di errore chiaro con usage hints e cartelle disponibili."""
    _flushed_print("[ERRORE] Argomento mancante. È necessario specificare una cartella del compito o usare --batch.\n")
    _flushed_print("Uso corretto:")
    _flushed_print(f'  python main.py "elaborazioni/NOME_CARTELLA"   # modalità singolo compito (default)')
    _flushed_print(f"  python main.py --batch                         # modalità batch\n")
    _flushed_print(f"  python main.py --clear                         # pulisce tutti i file di precedenti correzioni\n")
    _flushed_print(f"Cartelle disponibili in {ELABORAZIONI_DIR}/:")
    if ELABORAZIONI_DIR.exists():
        for d in sorted(ELABORAZIONI_DIR.iterdir()):
            if d.is_dir():
                _flushed_print(f"  - elaborazioni/{d.name}")
    else:
        _flushed_print(f"  [ATTENZIONE] Cartella {ELABORAZIONI_DIR} non trovata.")
    sys.exit(1)


# --- Entry point ---

def main() -> None:
    """Entry point principale."""
    mancanti = _verifica_dipendenze()

    critiche = {"openai", "openpyxl"}
    critiche_mancanti = {pkg for pkg, _ in mancanti if pkg in critiche}

    if critiche_mancanti:
        _flushed_print(
            f"[ERRORE] Dipendenze critiche mancanti: "
            f"{', '.join(sorted(critiche_mancanti))}. Impossibile procedere."
        )
        sys.exit(1)

    if any(pkg == "aspose-words" for pkg, _ in mancanti):
        _flushed_print(
            "[ATTENZIONE] aspose-words non installato: i file .doc legacy "
            "non potranno essere convertiti. Proseguo comunque.\n"
        )

    args = _parse_args()

    if args.batch:
        _elabora_batch(clear=args.clear)
        return

    if not args.cartella_compito:
        _show_usage_error_and_exit()

    compito_path = Path(args.cartella_compito).resolve()
    if not compito_path.is_dir():
        _flushed_print(f"[ERRORE] Percorso '{args.cartella_compito}' non trovato o non è una cartella.\n")
        if not args.cartella_compito.startswith("elaborazioni"):
            _flushed_print(f"[INFO] Suggerimento: usa un percorso relativo, es. elaborazioni/{args.cartella_compito}")
        _flushed_print(f"[INFO] Cartelle disponibili in {ELABORAZIONI_DIR}/:")
        if ELABORAZIONI_DIR.exists():
            for d in sorted(ELABORAZIONI_DIR.iterdir()):
                if d.is_dir():
                    _flushed_print(f"  - elaborazioni/{d.name}")
        sys.exit(1)

    global BASE_DIR, CONSEGNE_DIR, CORREZIONI_DIR
    BASE_DIR = compito_path
    CONSEGNE_DIR = BASE_DIR / "consegne"
    CORREZIONI_DIR = BASE_DIR / "correzioni"

    successo = _elabora_compito_singolo(clear=args.clear)
    if successo:
        # Evita duplicazione in report.log se --clear su compito già registrato
        if args.clear and compito_path.name in _carica_elaborazioni_completate():
            pass
        else:
            _registra_elaborazione(compito_path.name)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
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
from converters import get_converter
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

def _verifica_dipendenze() -> None:
    """Verifica che i moduli Python richiesti siano installati.

    Controlla la disponibilità di: openpyxl, openai, aspose.words.
    Per ogni modulo mancante, stampa un warning con le istruzioni di installazione.
    Non interrompe l'esecuzione se un modulo è mancante.
    """
    dipendenze = {
        "openpyxl": ("openpyxl", ".xlsx"),
        "openai": ("openai", "interazione LLM"),
        "aspose.words": ("aspose-words", ".doc"),
    }

    mancanti = []
    for modulo, (pacchetto, descrizione) in dipendenze.items():
        try:
            __import__(modulo)
        except ImportError:
            mancanti.append((pacchetto, descrizione))

    if mancanti:
        print("\n" + "=" * 60)
        print("ATTENZIONE: Dipendenze mancanti rilevate")
        print("=" * 60)
        print("Il programma potrebbe non funzionare correttamente.")
        print("Installa i moduli mancanti con:\n")
        for pacchetto, descrizione in mancanti:
            print(f"  - {pacchetto}: pip install {pacchetto}  (per {descrizione})")
        print("\n")


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


def _raggruppa_file_per_studente(
    file_paths: list[Path],
) -> dict[str, list[Path]]:
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

    file_supportati = [
        f for f in file_paths
        if f.suffix.lower() in Settings.CONVERTERS_MAP
    ]

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

        funzione_conversione = get_converter(file_path.suffix)
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


def _processa_studente_multi_file(nome_studente: str, file_paths: list[Path]) -> str:
    """Processa più file per uno stesso studente e restituisce un markdown unificato.

    Args:
        nome_studente: Nome canonico dello studente (es. 'Mario Rossi').
        file_paths: Lista di Path dei file da convertire.

    Returns:
        Markdown unificato con intestazione dello studente e contenuto di
        tutti i file convertiti, separati da intestazioni di file.
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

        funzione_conversione = get_converter(file_path.suffix)
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

    return "\n".join(parti)


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
    funzione_conversione = get_converter(file_path.suffix)
    if not funzione_conversione:
        logger.warning("Nessun convertitore disponibile per %s", file_path.name)
        return ""

    try:
        return funzione_conversione(file_path)
    except Exception as e:
        logger.warning("Errore nella conversione di %s: %s", file_path.name, e)
        return ""


def _crea_traccia_combinata() -> str:
    """Crea traccia.md combinato in tmp/ con traccia originale + allegati convertiti."""
    traccia_originale = (BASE_DIR / "traccia.md").read_text(encoding="utf-8")
    allegati = _trova_allegati_traccia()

    if not allegati:
        combined_path = TMP_DIR / "traccia.md"
        combined_path.write_text(traccia_originale, encoding="utf-8")
        print(f"  -> Salvata traccia.md (senza allegati) in tmp/")
        return traccia_originale

    parti = [traccia_originale]
    for allegato_path in allegati:
        contenuto_markdown = _converti_allegato(allegato_path)
        if contenuto_markdown:
            parti.append(f"\n\n## Allegato: {allegato_path.name}\n\n{contenuto_markdown}")
            tmp_output_path = TMP_DIR / f"traccia_{allegato_path.stem}.md"
            tmp_output_path.write_text(contenuto_markdown, encoding="utf-8")
            print(f"     -> Salvato allegato: {tmp_output_path.name}")
        else:
            print(f"     [ATTENZIONE] Nessun contenuto estratto da {allegato_path.name}")

    contenuto_completo = "\n".join(parti)
    combined_path = TMP_DIR / "traccia.md"
    combined_path.write_text(contenuto_completo, encoding="utf-8")
    print(f"  -> Salvata traccia.md combinata ({len(allegati)} allegato/i) in tmp/")
    return contenuto_completo


# --- Pipeline principale ---

def _elabora_griglia() -> str:
    """Genera la griglia di valutazione dalla traccia e allegati opzionali."""
    global TMP_DIR
    print("[1/4] Generazione della griglia di valutazione dalla traccia...")

    prompt_griglia_tmpl = _carica_prompt("prompt_griglia.md")
    contenuto_prompt = _crea_traccia_combinata()

    griglia_contenuto = call_llm(
        [{"role": "user", "content": prompt_griglia_tmpl.replace("{traccia}", contenuto_prompt)}]
    )

    (BASE_DIR / "griglia_valutazione.md").write_text(griglia_contenuto, encoding="utf-8")
    print("-> Griglia generata.\n")
    return griglia_contenuto


def _elabora_consegne(griglia: str) -> str:
    """Elabora tutte le consegne degli studenti e restituisce il testo concatenato.

    Supporta due modalità:
    - Cartelle studente: directory in consegne/ elaborate con _processa_studente_folder()
    - File multipli: file in consegne/ raggruppati per studente con _raggruppa_file_per_studente()
      e poi elaborati con _processa_studente_multi_file()

    Entrambi i percorsi producono un markdown unificato per studente.
    I nomi "canonici" vengono usati come chiave per il rilevamento dei conflitti
    tra cartelle e file (es. cartella "rossi mario" e file "ROSSI MARIO - 1.docx").
    """
    print("[2/4] Elaborazione delle consegne degli studenti...")
    prompt_corr_tmpl = _carica_prompt("prompt_correzione.md")
    tutte_le_correzioni_text = ""
    studenti_elaborati = 0

    if not CONSEGNE_DIR.exists():
        print(f"  [ATTENZIONE] Cartella {CONSEGNE_DIR} non trovata.")
        return ""

    # studenti_da_elaborare: nome_canonico → (markdown, origine)
    studenti_da_elaborare: dict[str, tuple[str, str]] = {}

    # 1) Cartelle studente
    cartelle: list[Path] = []
    file_singoli: list[Path] = []
    for item in CONSEGNE_DIR.iterdir():
        if item.is_dir():
            cartelle.append(item)
        elif item.is_file():
            if item.suffix.lower() not in Settings.CONVERTERS_MAP:
                print(f"  [SKIP] File non supportato: {item.name}")
                continue
            file_singoli.append(item)

    for cartella in cartelle:
        print(f" -> Rilevata cartella studente: {cartella.name}")
        try:
            nome, markdown = _processa_studente_folder(cartella)
        except ValueError as e:
            print(f"    [ATTENZIONE] Cartella saltata {cartella.name}: {e}")
            continue
        except Exception as e:
            print(f"    [ERRORE] Errore nella cartella {cartella.name}: {e}")
            continue

        origine = f"cartella:{cartella.name}"
        if nome in studenti_da_elaborare:
            vecchio_md, vecchia_origine = studenti_da_elaborare[nome]
            logger.warning(
                "Conflitto nome studente '%s': già presente da [%s], "
                "aggiungo contenuto da [%s]",
                nome,
                vecchia_origine,
                origine,
            )
            # Concatena: la cartella + i file dello studente sono complementari
            studenti_da_elaborare[nome] = (
                vecchio_md + "\n\n" + markdown,
                f"{vecchia_origine}; {origine}",
            )
        else:
            studenti_da_elaborare[nome] = (markdown, origine)

        print(f"    -> Cartella elaborata: {nome}")

    # 2) File singoli raggruppati per studente
    if file_singoli:
        print(f" -> Rilevati {len(file_singoli)} file singoli da raggruppare...")
        raggruppato = _raggruppa_file_per_studente(file_singoli)

        for nome_canonico, paths in raggruppato.items():
            print(
                f" -> Studente: {nome_canonico} "
                f"({len(paths)} file: {', '.join(p.name for p in paths)})"
            )
            try:
                markdown = _processa_studente_multi_file(nome_canonico, paths)
            except Exception as e:
                print(f"    [ERRORE] Impossibile elaborare {nome_canonico}: {e}")
                continue

            origine = f"file:{', '.join(p.name for p in paths)}"
            if nome_canonico in studenti_da_elaborare:
                vecchio_md, vecchia_origine = studenti_da_elaborare[nome_canonico]
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
                )
            else:
                studenti_da_elaborare[nome_canonico] = (markdown, origine)

    if not studenti_da_elaborare:
        print(f"  [ATTENZIONE] Nessuno studente da elaborare in {CONSEGNE_DIR}.")
        return ""

    # 3) Correzione LLM per ogni studente
    for nome_studente, (markdown_contenuto, origine) in studenti_da_elaborare.items():
        nome_file_sicuro = re.sub(r'[^\w\s-]', '', nome_studente).strip().replace(' ', '_')
        tmp_output_path = TMP_DIR / f"consegna_{nome_file_sicuro}.md"
        tmp_output_path.write_text(markdown_contenuto, encoding="utf-8")
        print(f"    -> Salvato markdown unificato: {tmp_output_path.name}")

        try:
            prompt_correzione_finale = (
                prompt_corr_tmpl
                .replace("{nome_studente}", nome_studente)
                .replace("{griglia}", griglia)
                .replace("{dati_studente}", markdown_contenuto)
            )

            report_studente = call_llm(
                [{"role": "user", "content": prompt_correzione_finale}]
            )

            output_path = CORREZIONI_DIR / f"{nome_studente} - griglia.md"
            output_path.write_text(report_studente, encoding="utf-8")

            tutte_le_correzioni_text += (
                f"\n\n--- STUDENTE: {nome_studente} ---\n" + report_studente
            )
            studenti_elaborati += 1

        except Exception as e:
            print(f"    [ERRORE] Impossibile correggere {nome_studente}: {e}")
            continue

    print(f"  -> {studenti_elaborati} studente/i elaborati.\n")
    return tutte_le_correzioni_text


def _genera_riepilogo(tutte_le_correzioni: str) -> None:
    """Genera il riepilogo finale delle valutazioni."""
    print("[3/4] Generazione del report di riepilogo finale...")

    if not tutte_le_correzioni.strip():
        print("  [ATTENZIONE] Nessuna correzione da riepilogare.")
        return

    prompt_riepilogo_tmpl = _carica_prompt("prompt_riepilogo.md")

    riepilogo_contenuto = call_llm(
        [{"role": "user", "content": prompt_riepilogo_tmpl.replace(
            "{tutte_le_correzioni}", tutte_le_correzioni
        )}]
    )

    (BASE_DIR / "riepilogo.md").write_text(riepilogo_contenuto, encoding="utf-8")
    print("[4/4] Pipeline completata con successo!\n")


def _elabora_compito_singolo() -> bool:
    """Elabora un singolo compito nella working directory."""
    global TMP_DIR
    print("=" * 60)
    print("AiGrader - Modalità Compito Singolo")
    print("=" * 60)

    CORREZIONI_DIR.mkdir(exist_ok=True)
    TMP_DIR = BASE_DIR / "tmp"
    TMP_DIR.mkdir(exist_ok=True)

    try:
        griglia = _elabora_griglia()
        tutte_le_correzioni = _elabora_consegne(griglia)
        _genera_riepilogo(tutte_le_correzioni)
        return True
    except FileNotFoundError as e:
        print(f"\n[ERRORE] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERRORE] Errore durante la pipeline: {e}")
        sys.exit(1)


def _elabora_batch() -> None:
    """Elabora tutti i compiti nella cartella elaborazioni/."""
    print("=" * 60)
    print("AiGrader - Modalità Batch")
    print("=" * 60)

    if not ELABORAZIONI_DIR.exists():
        print(f"[ERRORE] Cartella {ELABORAZIONI_DIR} non trovata.")
        sys.exit(1)

    completate = _carica_elaborazioni_completate()

    task_dirs = [d for d in ELABORAZIONI_DIR.iterdir() if d.is_dir()]
    if not task_dirs:
        print(f"[ATTENZIONE] Nessuna sottocartella trovata in {ELABORAZIONI_DIR}.")
        sys.exit(0)

    da_elaborare = [d for d in task_dirs if d.name not in completate]
    saltate = len(task_dirs) - len(da_elaborare)

    if saltate:
        print(f"Saltate {saltate} elaborazione/i già completate (report.log).\n")

    if not da_elaborare:
        print("[INFO] Tutti i compiti sono già stati elaborati.")
        sys.exit(0)

    print(f"Trovati {len(task_dirs)} compito/i totali, {len(da_elaborare)} da elaborare.\n")

    elaborati = 0
    for task_dir in da_elaborare:
        print(f"\n{'#' * 60}")
        print(f"# Compito: {task_dir.name}")
        print(f"{'#' * 60}")

        global BASE_DIR, CONSEGNE_DIR, CORREZIONI_DIR
        BASE_DIR = task_dir
        CONSEGNE_DIR = BASE_DIR / "consegne"
        CORREZIONI_DIR = BASE_DIR / "correzioni"

        try:
            successo = _elabora_compito_singolo()
            if successo:
                _registra_elaborazione(task_dir.name)
                elaborati += 1
        except FileNotFoundError as e:
            print(f"[ERRORE] Compito {task_dir.name} saltato: {e}")
        except Exception as e:
            print(f"[ERRORE] Compito {task_dir.name} fallito: {e}")
        finally:
            TMP_DIR = None

    print(f"\n{'=' * 60}")
    print(f"Batch completato: {elaborati} compito/i elaborati.")
    print(f"{'=' * 60}")


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

    return parser.parse_args()


def _show_usage_error_and_exit() -> None:
    """Mostra un messaggio di errore chiaro con usage hints e cartelle disponibili."""
    print("[ERRORE] Argomento mancante. È necessario specificare una cartella del compito o usare --batch.\n")
    print("Uso corretto:")
    print(f'  python main.py "elaborazioni/NOME_CARTELLA"   # modalità singolo compito (default)')
    print(f"  python main.py --batch                         # modalità batch\n")
    print(f"Cartelle disponibili in {ELABORAZIONI_DIR}/:")
    if ELABORAZIONI_DIR.exists():
        for d in sorted(ELABORAZIONI_DIR.iterdir()):
            if d.is_dir():
                print(f"  - elaborazioni/{d.name}")
    else:
        print(f"  [ATTENZIONE] Cartella {ELABORAZIONI_DIR} non trovata.")
    sys.exit(1)


# --- Entry point ---

def main() -> None:
    """Entry point principale."""
    _verifica_dipendenze()
    args = _parse_args()

    if args.batch:
        _elabora_batch()
        return

    if not args.cartella_compito:
        _show_usage_error_and_exit()

    compito_path = Path(args.cartella_compito).resolve()
    if not compito_path.is_dir():
        print(f"[ERRORE] Percorso '{args.cartella_compito}' non trovato o non è una cartella.\n")
        if not args.cartella_compito.startswith("elaborazioni"):
            print(f"[INFO] Suggerimento: usa un percorso relativo, es. elaborazioni/{args.cartella_compito}")
        print(f"[INFO] Cartelle disponibili in {ELABORAZIONI_DIR}/:")
        if ELABORAZIONI_DIR.exists():
            for d in sorted(ELABORAZIONI_DIR.iterdir()):
                if d.is_dir():
                    print(f"  - elaborazioni/{d.name}")
        sys.exit(1)

    global BASE_DIR, CONSEGNE_DIR, CORREZIONI_DIR
    BASE_DIR = compito_path
    CONSEGNE_DIR = BASE_DIR / "consegne"
    CORREZIONI_DIR = BASE_DIR / "correzioni"

    _elabora_compito_singolo()
    _registra_elaborazione(compito_path.name)


if __name__ == "__main__":
    main()
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
from pathlib import Path

from config.settings import Settings
from converters import get_converter
from utils.llm_client import call_llm

# --- Logger per warning di conversione ---
logger = logging.getLogger(__name__)

# --- Costanti ---
BASE_DIR = Path(".")
PROMPT_DIR = BASE_DIR / "prompts"
CONSEGNE_DIR = BASE_DIR / "consegne"
CORREZIONI_DIR = BASE_DIR / "correzioni"
ELABORAZIONI_DIR = Path("elaborazioni")
REPORT_LOG = ELABORAZIONI_DIR / "report.log"  # Registro delle elaborazioni completate
TMP_DIR = None  # Cartella tmp per allegati e markdown convertiti (inizializzata per compito)


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
            # Salta righe vuote e commenti
            if not linea or linea.startswith("#"):
                continue
            completate.add(linea)
    return completate


def _registra_elaborazione(nome_cartella: str) -> None:
    """Appende il nome della cartella a report.log dopo una valutazione completata."""
    REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{nome_cartella}\n")


def _estrai_nome_studente(nome_file: str) -> str:
    """Estrae il nome dello studente dal nome del file.

    Supporta formati come:
    - "rossi mario - bilancio" → "Rossi Mario"
    - "bianchi luca.docx" → "Bianchi Luca"

    Se il file non segue il formato atteso (senza '-'), usa lo stem come nome.
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
            # Rimuovi il '-' iniziale prima di usare il fallback
            stem_pulito = stem.lstrip('- ').strip()
            if stem_pulito:
                return stem_pulito.title()
            return stem.title()
        return nome.title()
    # File senza '-': usa lo stem come nome studente
    return stem.title()


def _raggruppa_file_per_studente(file_paths: list[Path]) -> dict[str, list[Path]]:
    """Raggruppa i file per studente basandosi sul prefisso del nome.

    Scansiona una lista di Path, estrae il nome studente per ciascun file
    usando _estrai_nome_studente(), e restituisce un dict che mappa ogni
    nome studente alla lista dei suoi file.

    Supporta formati come:
    - "rossi mario - a.docx" e "rossi mario - b.xlsx" → stesso gruppo "Rossi Mario"
    - "bianchi luca.docx" → gruppo "Bianchi Luca" (usa lo stem come nome)

    Args:
        file_paths: Lista di Path dei file degli studenti.

    Returns:
        Dict che mappa il nome dello studente (str) alla lista dei suoi file (Path).
    """
    raggruppato: dict[str, list[Path]] = {}

    for file_path in file_paths:
        nome_studente = _estrai_nome_studente(file_path.name)
        if nome_studente not in raggruppato:
            raggruppato[nome_studente] = []
        raggruppato[nome_studente].append(file_path)

    return raggruppato


def _processa_studente_folder(folder_path: Path) -> tuple[str, str]:
    """Processa una cartella studente in modo ricorsivo.

    Scansiona tutti i file nella cartella (e sottocartelle), converte ciascun
    file supportato, e restituisce un markdown unificato. Il nome studente
    è estratto dal nome della cartella.

    Args:
        folder_path: Percorso della cartella dello studente.

    Returns:
        Tuple (nome_studente, markdown_contenuto) dove nome_studente è il
        nome della cartella in title case e markdown_contenuto è il contenuto
        markdown unificato di tutti i file convertiti.

    Raises:
        ValueError: Se la cartella non esiste, è vuota o non contiene file
                    supportati.
    """
    if not folder_path.exists():
        raise ValueError(f"Cartella non trovata: {folder_path}")

    if not folder_path.is_dir():
        raise ValueError(f"Percorso non è una cartella: {folder_path}")

    # Estrai il nome studente dal nome della cartella
    nome_studente = folder_path.name.title()

    # Scansiona ricorsivamente tutti i file
    file_paths = sorted(
        [f for f in folder_path.rglob("*") if f.is_file()],
        key=lambda p: str(p.relative_to(folder_path)).lower(),
    )

    if not file_paths:
        raise ValueError(f"Cartella vuota: {folder_path}")

    # Filtra solo i file con estensioni supportate
    file_supportati = [
        f for f in file_paths
        if f.suffix.lower() in Settings.CONVERTERS_MAP
    ]

    # Rileva file duplicati (stesso nome ma percorsi diversi)
    nomi_file_visti: dict[str, list[Path]] = {}
    file_duplicati: list[Path] = []
    for f in file_supportati:
        nome_base = f.name
        if nome_base not in nomi_file_visti:
            nomi_file_visti[nome_base] = []
        nomi_file_visti[nome_base].append(f)

    for nome, paths in nomi_file_visti.items():
        if len(paths) > 1:
            # File duplicati: usa il percorso completo come identificatore
            file_duplicati.extend(paths)
            logger.warning(
                "File duplicati rilevati in %s: %s",
                folder_path.name,
                ", ".join(p.name for p in paths),
            )

    if not file_supportati:
        raise ValueError(
            f"Nessun file supportato trovato in {folder_path}. "
            f"Estensioni supportate: {', '.join(Settings.CONVERTERS_MAP.keys())}"
        )

    # Costruisci il markdown
    parti: list[str] = [f"# Consegna di {nome_studente}\n"]

    # Tieni traccia dei nomi file usati per evitare duplicati
    nomi_file_usati: set[str] = set()

    for file_path in file_supportati:
        # Calcola il percorso relativo dalla cartella studente
        percorso_rel = file_path.relative_to(folder_path)
        nome_file_display = str(percorso_rel)

        # Gestisci file duplicati: se il nome è già usato, aggiungi il percorso
        if nome_file_display in nomi_file_usati:
            # Usa il percorso completo come identificatore
            nome_file_display = str(file_path)
            logger.info(
                "File duplicato rilevato, uso percorso completo: %s",
                nome_file_display,
            )
        nomi_file_usati.add(nome_file_display)

        parte_markdown = f"## File: {nome_file_display}\n\n"

        # Cerca convertitore per l'estensione
        funzione_conversione = get_converter(file_path.suffix)
        if not funzione_conversione:
            parte_markdown += f"[Errore nella conversione: Nessun convertitore disponibile per {file_path.suffix}]\n"
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

    Per ogni file nella lista, invoca il convertitore appropriato tramite
    get_converter() e raccoglie i risultati separandoli con intestazioni
    '## File: [nome_file]'. Gli errori di conversione sono riportati come
    '[Errore nella conversione: messaggio]'. I file sono elaborati in ordine
    alfabetico.

    Args:
        nome_studente: Nome completo dello studente (es. 'Mario Rossi').
        file_paths: Lista di Path dei file da convertire.

    Returns:
        Markdown unificato con intestazione dello studente e contenuto di
        tutti i file convertiti, separati da intestazioni di file.
    """
    # Ordina i file in ordine alfabetico per nome
    file_paths_ordinate = sorted(file_paths, key=lambda p: p.name.lower())

    parti: list[str] = [f"# Consegna di {nome_studente}\n"]

    # Tieni traccia dei nomi file usati per evitare duplicati
    nomi_file_usati: set[str] = set()

    for file_path in file_paths_ordinate:
        nome_file_display = file_path.name

        # Gestisci file duplicati: se il nome è già usato, usa il nome completo
        if nome_file_display in nomi_file_usati:
            nome_file_display = file_path.name
            logger.warning(
                "File duplicato rilevato per %s: %s",
                nome_studente,
                nome_file_display,
            )
        nomi_file_usati.add(nome_file_display)

        parte_markdown = f"## File: {nome_file_display}\n\n"

        # Cerca convertitore per l'estensione
        funzione_conversione = get_converter(file_path.suffix)
        if not funzione_conversione:
            parte_markdown += f"[Errore nella conversione: Nessun convertitore disponibile per {file_path.suffix}]\n"
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
    # Pattern: [[allegato: nomefile.ext]]
    pattern = r"\[\[allegato:\s*([^\]]+)\]\]"
    return re.findall(pattern, contenuto, re.IGNORECASE)


def _trova_allegati_traccia() -> list[Path]:
    """Trova file allegati alla traccia nella working directory.

    Cerca in due modi:
    1. File con prefisso 'traccia' e estensioni supportate (.xlsx, .docx, .doc, .html)
    2. File referenziati esplicitamente in traccia.md con la sintassi [[allegato: nomefile.ext]]

    Restituisce una lista ordinata e deduplicata di Path.
    """
    allegati_supportati = {".xlsx", ".docx", ".doc", ".html"}
    allegati: dict[str, Path] = {}  # usa dict per deduplicare per nome

    if not BASE_DIR.exists():
        return []

    # 1. Cerca file con prefisso 'traccia'
    for file_path in sorted(BASE_DIR.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.name.lower().startswith("traccia") and file_path.suffix.lower() in allegati_supportati:
            allegati[file_path.name.lower()] = file_path

    # 2. Cerca riferimenti espliciti in traccia.md
    riferimenti = _estrai_riferimenti_allegati_traccia()
    for nome_file in riferimenti:
        nome_file_pulito = nome_file.strip()
        percorso_allegato = BASE_DIR / nome_file_pulito
        if percorso_allegato.is_file() and percorso_allegato.suffix.lower() in allegati_supportati:
            allegati[nome_file_pulito.lower()] = percorso_allegato

    return list(allegati.values())


def _converti_allegato(file_path: Path) -> str:
    """Converte un allegato in markdown usando il convertitore appropriato.

    Restituisce il contenuto markdown o stringa vuota in caso di errore.
    """
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
    """Crea un file traccia.md combinato in tmp/ con traccia originale + allegati convertiti.

    Restituisce il contenuto completo della traccia combinata.
    """
    traccia_originale = (BASE_DIR / "traccia.md").read_text(encoding="utf-8")
    allegati = _trova_allegati_traccia()

    if not allegati:
        # Nessun allegato: salva la traccia originale così com'è
        combined_path = TMP_DIR / "traccia.md"
        combined_path.write_text(traccia_originale, encoding="utf-8")
        print(f"  -> Salvata traccia.md (senza allegati) in tmp/")
        return traccia_originale

    # Costruisce il contenuto combinato
    parti = [traccia_originale]
    for allegato_path in allegati:
        contenuto_markdown = _converti_allegato(allegato_path)
        if contenuto_markdown:
            parti.append(f"\n\n## Allegato: {allegato_path.name}\n\n{contenuto_markdown}")
            # Salva anche il singolo allegato convertito in tmp/
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


def _elabora_griglia() -> str:
    """Genera la griglia di valutazione dalla traccia e allegati opzionali.

    Crea un file traccia.md combinato in tmp/ con traccia originale + allegati convertiti,
    poi usa questo contenuto per generare la griglia tramite LLM.
    """
    global TMP_DIR
    print("[1/4] Generazione della griglia di valutazione dalla traccia...")

    prompt_griglia_tmpl = _carica_prompt("prompt_griglia.md")

    # Crea la traccia combinata (traccia + allegati convertiti) in tmp/
    contenuto_prompt = _crea_traccia_combinata()

    griglia_contenuto = call_llm(
        [{"role": "user", "content": prompt_griglia_tmpl.replace("{traccia}", contenuto_prompt)}]
    )

    (BASE_DIR / "griglia_valutazione.md").write_text(griglia_contenuto, encoding="utf-8")
    print("-> Griglia generata.\n")
    return griglia_contenuto


def _elabora_consegne(griglia: str) -> str:
    """Elabora tutte le consegne degli studenti (cartelle e/o file) e restituisce il testo concatenato.

    Supporta due modalità:
    - Cartelle studente: directory in consegne/ elaborate con _processa_studente_folder()
    - File multipli: file in consegne/ raggruppati per studente con _raggruppa_file_per_studente()
      e poi elaborati con _processa_studente_multi_file()

    Entrambi i percorsi producono un markdown unificato per studente.
    """
    print("[2/4] Elaborazione delle consegne degli studenti...")
    prompt_corr_tmpl = _carica_prompt("prompt_correzione.md")
    tutte_le_correzioni_text = ""
    studenti_elaborati = 0

    if not CONSEGNE_DIR.exists():
        print(f"  [ATTENZIONE] Cartella {CONSEGNE_DIR} non trovata.")
        return ""

    # Raccoglie gli studenti da elaborare: {nome_studente: markdown_contenuto}
    # Tiene traccia di dove è stato trovato ogni studente per rilevare conflitti
    studenti_rilevati: dict[str, list[str]] = {}  # {nome: [origine]}
    studenti_da_elaborare: dict[str, str] = {}
    file_paths_da_elaborare: list[Path] = []

    for item in CONSEGNE_DIR.iterdir():
        if item.is_dir():
            # Processa come cartella studente
            print(f" -> Rilevata cartella studente: {item.name}")
            try:
                nome, markdown = _processa_studente_folder(item)
                origine = f"cartella:{item.name}"

                # Controlla conflitti: stesso nome studente già trovato
                if nome in studenti_rilevati:
                    origini_precedenti = "; ".join(studenti_rilevati[nome])
                    print(
                        f"    [ATTENZIONE] Conflitto nome studente: '{nome}' già trovato in [{origini_precedenti}]. "
                        f"Sovrascrivo con {origine}"
                    )
                    logger.warning(
                        "Conflitto nome studente '%s': già presente in [%s], sovrascrivo con %s",
                        nome,
                        origini_precedenti,
                        origine,
                    )
                else:
                    studenti_rilevati[nome] = []

                studenti_rilevati[nome].append(origine)
                studenti_da_elaborare[nome] = markdown
                print(f"    -> Cartella elaborata: {nome} ({markdown.count('## File:') + 1} file)")
            except ValueError as e:
                print(f"    [ATTENZIONE] Cartella saltata {item.name}: {e}")
                continue
            except Exception as e:
                print(f"    [ERRORE] Errore nella cartella {item.name}: {e}")
                continue

        elif item.is_file():
            # File: controlla se ha estensione supportata e raccoglilo
            if item.suffix.lower() not in Settings.CONVERTERS_MAP:
                print(f"  [SKIP] File non supportato: {item.name}")
                continue
            file_paths_da_elaborare.append(item)

    if file_paths_da_elaborare:
        print(f" -> Rilevati {len(file_paths_da_elaborare)} file da elaborare...")
        # Raggruppa i file per studente
        raggruppato = _raggruppa_file_per_studente(file_paths_da_elaborare)

        for nome_studente, paths in raggruppato.items():
            print(f" -> Elaboro studente: {nome_studente} ({len(paths)} file)")
            try:
                markdown = _processa_studente_multi_file(nome_studente, paths)
                origine = f"file:{', '.join(p.name for p in paths)}"

                # Controlla conflitti: stesso nome studente già trovato
                if nome_studente in studenti_rilevati:
                    origini_precedenti = "; ".join(studenti_rilevati[nome_studente])
                    print(
                        f"    [ATTENZIONE] Conflitto nome studente: '{nome_studente}' già trovato in [{origini_precedenti}]. "
                        f"Sovrascrivo con {origine}"
                    )
                    logger.warning(
                        "Conflitto nome studente '%s': già presente in [%s], sovrascrivo con %s",
                        nome_studente,
                        origini_precedenti,
                        origine,
                    )
                else:
                    studenti_rilevati[nome_studente] = []

                studenti_rilevati[nome_studente].append(origine)
                studenti_da_elaborare[nome_studente] = markdown
                print(f"    -> Markdown unificato generato per {nome_studente}")
            except Exception as e:
                print(f"    [ERRORE] Impossibile elaborare {nome_studente}: {e}")
                continue

    if not studenti_da_elaborare:
        print(f"  [ATTENZIONE] Nessun studente da elaborare in {CONSEGNE_DIR}.")
        return ""

    # Elabora tutti gli studenti con i loro markdown unificati
    for nome_studente, markdown_contenuto in studenti_da_elaborare.items():
        # Salva il markdown unificato nella cartella tmp
        # Usa un nome file sicuro (sostituisci spazi e caratteri speciali)
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

            # Salva la griglia di correzione
            output_path = CORREZIONI_DIR / f"{nome_studente} - griglia.md"
            output_path.write_text(report_studente, encoding="utf-8")

            tutte_le_correzioni_text += (
                f"\n\n--- STUDENTE: {nome_studente} ---\n" + report_studente
            )
            studenti_elaborati += 1

        except Exception as e:
            print(f"    [ERRORE] Impossibile correggere {nome_studente}: {e}")
            continue

    # Riepilogo conflitti e studenti saltati
    if studenti_rilevati:
        conflitti = {n: o for n, o in studenti_rilevati.items() if len(o) > 1}
        if conflitti:
            print(f"  [INFO] Conflitti nome studente rilevati: {len(conflitti)}")
            for nome, origini in conflitti.items():
                print(f"    - '{nome}': {', '.join(origini)}")

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
    """Elabora un singolo compito nella working directory.
    
    Restituisce True se la pipeline è completata con successo.
    """
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

    # Legge le elaborazioni già completate
    completate = _carica_elaborazioni_completate()

    task_dirs = [d for d in ELABORAZIONI_DIR.iterdir() if d.is_dir()]
    if not task_dirs:
        print(f"[ATTENZIONE] Nessuna sottocartella trovata in {ELABORAZIONI_DIR}.")
        sys.exit(0)

    # Filtra le cartelle già valutate
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

        # Cambia working directory per questo compito
        global BASE_DIR, CONSEGNE_DIR, CORREZIONI_DIR
        BASE_DIR = task_dir
        CONSEGNE_DIR = BASE_DIR / "consegne"
        CORREZIONI_DIR = BASE_DIR / "correzioni"

        try:
            successo = _elabora_compito_singolo()
            if successo:
                # Registra la cartella come completata
                _registra_elaborazione(task_dir.name)
                elaborati += 1
        except FileNotFoundError as e:
            print(f"[ERRORE] Compito {task_dir.name} saltato: {e}")
        except Exception as e:
            print(f"[ERRORE] Compito {task_dir.name} fallito: {e}")
        finally:
            # Reset TMP_DIR per il prossimo compito
            TMP_DIR = None

    print(f"\n{'=' * 60}")
    print(f"Batch completato: {elaborati} compito/i elaborati.")
    print(f"{'=' * 60}")


# --- Argparse ---

def _parse_args() -> argparse.Namespace:
    """Parsa i parametri da riga di comando.
    
    Restituisce un namespace con:
    - cartella_compito: percorso del compito (modalità singolo, default)
    - batch: flag per modalità batch
    """
    parser = argparse.ArgumentParser(
        description="AiGrader - Correzione automatica di compiti scolastici.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  python main.py "elaborazioni/4RIM - Esercitazione di Google Fogli"  # Elaborazione compito specifico (default)
  python main.py --batch                                              # Elaborazione batch di tutti i compiti
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

    # Se --batch è specificato, esegui modalità batch
    if args.batch:
        _elabora_batch()
        return

    # Se non è specificata nessuna cartella, mostra errore con usage hints
    if not args.cartella_compito:
        _show_usage_error_and_exit()

    # Il parametro è un percorso relativo alla CWD (es. elaborazioni/4RIM_test)
    compito_path = Path(args.cartella_compito).resolve()
    if not compito_path.is_dir():
        print(f"[ERRORE] Percorso '{args.cartella_compito}' non trovato o non è una cartella.\n")
        # Suggerisci percorsi se sembra un nome di cartella in elaborazioni/
        if not args.cartella_compito.startswith("elaborazioni"):
            print(f"[INFO] Suggerimento: usa un percorso relativo, es. elaborazioni/{args.cartella_compito}")
        print(f"[INFO] Cartelle disponibili in {ELABORAZIONI_DIR}/:")
        if ELABORAZIONI_DIR.exists():
            for d in sorted(ELABORAZIONI_DIR.iterdir()):
                if d.is_dir():
                    print(f"  - elaborazioni/{d.name}")
        sys.exit(1)

    # Cambia BASE_DIR verso la cartella del compito
    global BASE_DIR, CONSEGNE_DIR, CORREZIONI_DIR
    BASE_DIR = compito_path
    CONSEGNE_DIR = BASE_DIR / "consegne"
    CORREZIONI_DIR = BASE_DIR / "correzioni"

    _elabora_compito_singolo()
    # Registra l'elaborazione come completata (usa il nome della cartella)
    _registra_elaborazione(compito_path.name)


if __name__ == "__main__":
    main()

"""Convertitore Word legacy (.doc) → Markdown per AiGrader.

Supporta i file .doc (Word 97-2003, formato binario) tramite:
1. antiword (prerequisito di sistema, installato via apt/brew/choco)
2. docx2txt (package pip, fallback)
3. textract (package pip, fallback ulteriore)

Se nessun tool è disponibile, viene restituito un errore chiaro
con le istruzioni per l'installazione.
"""

from pathlib import Path
import subprocess
import shutil


def _check_antiword_available() -> bool:
    """Verifica se antiword è installato e disponibile nel PATH.

    Returns:
        True se antiword è trovato nel PATH, False altrimenti.
    """
    return shutil.which("antiword") is not None


def _check_docx2txt_available() -> bool:
    """Verifica se il package Python docx2txt è installato.

    Returns:
        True se docx2txt è importabile, False altrimenti.
    """
    try:
        import docx2txt  # noqa: F401
        return True
    except ImportError:
        return False


def _check_textract_available() -> bool:
    """Verifica se il package Python textract è installato.

    Returns:
        True se textract è importabile, False altrimenti.
    """
    try:
        import textract  # noqa: F401
        return True
    except ImportError:
        return False


def _convert_with_antiword(file_path: Path) -> str:
    """Converte un file .doc usando antiword.

    antiword è un tool da riga di comando che estrae testo puro
    dai file Word 97-2003. Non supporta la formattazione markdown,
    quindi il testo viene strutturato in paragrafi.

    Args:
        file_path: Percorso al file .doc.

    Returns:
        Stringa di testo strutturato in paragrafi.

    Raises:
        RuntimeError: Se antiword restituisce un errore.
    """
    try:
        result = subprocess.run(
            ["antiword", str(file_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else "errore sconosciuto"
            raise RuntimeError(
                f"antiword fallito per {file_path}: {error_msg}"
            )
        return result.stdout
    except FileNotFoundError:
        raise RuntimeError("antiword non trovato. Installare con: apt install antiword (Linux), brew install antiword (macOS), choco install antiword (Windows)")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"antiword timeout dopo 30s per {file_path}")


def _convert_with_docx2txt(file_path: Path) -> str:
    """Converte un file .doc usando docx2txt.

    docx2txt è un package Python che estrae il testo dai file
    Word (sia .doc che .docx). Estrae solo testo, senza formattazione.

    Args:
        file_path: Percorso al file .doc.

    Returns:
        Stringa di testo strutturato in paragrafi.

    Raises:
        ImportError: Se docx2txt non è installato.
        RuntimeError: Se la conversione fallisce.
    """
    import docx2txt

    try:
        text = docx2txt.process(str(file_path))
        return text or ""
    except Exception as exc:
        raise RuntimeError(
            f"docx2txt fallito per {file_path}: {exc}"
        ) from exc


def _convert_with_textract(file_path: Path) -> str:
    """Converte un file .doc usando textract.

    textract è un package Python che supporta molti formati di file,
    inclusi i .doc legacy.

    Args:
        file_path: Percorso al file .doc.

    Returns:
        Stringa di testo decodificata in UTF-8.

    Raises:
        ImportError: Se textract non è installato.
        RuntimeError: Se la conversione fallisce.
    """
    import textract

    try:
        result = textract.process(str(file_path))
        # textract restituisce bytes, decodifica in UTF-8
        return result.decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(
            f"textract fallito per {file_path}: {exc}"
        ) from exc


def _text_to_markdown(text: str) -> str:
    """Converte testo grezzo in markdown di base.

    Applica una struttura semplice:
    - Paragrafi separati da nuove linee doppie
    - Rilevamento base di elenchi (righe che iniziano con -, *, o numeri)
    - Rilevamento base di tabelle (righe con pipe |)

    Args:
        text: Testo grezzo estratto dal file .doc.

    Returns:
        Stringa markdown con struttura di base.
    """
    if not text:
        return "*Documento .doc vuoto o senza contenuti leggibili.*\n"

    lines = text.split("\n")
    md_lines = []

    for line in lines:
        stripped = line.strip()

        # Riga vuota → separatore di paragrafo
        if not stripped:
            if md_lines and md_lines[-1] != "":
                md_lines.append("")
            continue

        # Rilevamento elenchi non ordinati (linee che iniziano con - o *)
        if stripped.startswith(("-", "*")) and len(stripped) > 1:
            md_lines.append(f"- {stripped[1:].strip()}")
            continue

        # Rilevamento elenchi ordinati (linee che iniziano con "1.", "2.", ecc.)
        import re
        ordered_match = re.match(r"(\d+)\.\s+(.*)", stripped)
        if ordered_match:
            md_lines.append(f"{ordered_match.group(1)}. {ordered_match.group(2)}")
            continue

        # Rilevamento tabelle (righe con pipe)
        if "|" in stripped:
            cells = [cell.strip() for cell in stripped.split("|")]
            # Aggiungi riga separatore dopo l'eventuale intestazione
            md_lines.append("| " + " | ".join(cells) + " |")
            continue

        # Testo normale → paragrafo
        md_lines.append(stripped)

    return "\n".join(md_lines) + "\n"


def converti(file_path: str | Path) -> str:
    """Converte un documento Word legacy (.doc) in una stringa Markdown.

    Prova i convertitori in ordine di priorità:
    1. antiword (tool di sistema)
    2. docx2txt (package pip)
    3. textract (package pip)

    Se nessun convertitore è disponibile, restituisce un errore chiaro
    con le istruzioni per l'installazione.

    Args:
        file_path: Percorso al file .doc (Word 97-2003, formato binario).

    Returns:
        Stringa Markdown con il contenuto del documento (paragrafi e tabelle di base).

    Raises:
        IOError: Se il file non esiste, non è leggibile, o è corrotto.
        RuntimeError: Se nessun convertitore disponibile è installato.
    """
    file_path = Path(file_path)

    # --- Validazione file ---
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")

    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")

    # Verifica che sia un file .doc (controllo estensione)
    if file_path.suffix.lower() != ".doc":
        raise IOError(
            f"Formato non supportato: {file_path.suffix}. "
            "Questo convertitore supporta solo file .doc legacy."
        )

    # --- Prova i convertitori in ordine di priorità ---
    converters = []

    if _check_antiword_available():
        converters.append(("antiword", _convert_with_antiword))
    if _check_docx2txt_available():
        converters.append(("docx2txt", _convert_with_docx2txt))
    if _check_textract_available():
        converters.append(("textract", _convert_with_textract))

    if not converters:
        install_instructions = []
        if not _check_antiword_available():
            install_instructions.append(
                "antiword (consigliato, migliore qualità): "
                "apt install antiword (Linux) / brew install antiword (macOS) / choco install antiword (Windows)"
            )
        if not _check_docx2txt_available():
            install_instructions.append(
                "docx2txt (fallback): pip install docx2txt"
            )
        if not _check_textract_available():
            install_instructions.append(
                "textract (fallback): pip install textract"
            )

        raise RuntimeError(
            f"Nessun convertitore .doc disponibile per {file_path}.\n\n"
            "Installa almeno uno dei seguenti tool:\n"
            + "\n".join(f"  - {inst}" for inst in install_instructions)
        )

    # --- Esegui conversione ---
    last_error = None
    for name, converter_func in converters:
        try:
            raw_text = converter_func(file_path)
            # Converte il testo grezzo in markdown di base
            md_output = _text_to_markdown(raw_text)
            return md_output
        except Exception as exc:
            last_error = exc
            # Prova il prossimo convertitore
            continue

    # Tutti i convertitori hanno fallito
    raise RuntimeError(
        f"Tutti i convertitori .doc hanno fallito per {file_path}.\n"
        f"Ultimo errore: {last_error}"
    )

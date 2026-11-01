"""Convertitore Web per AiGrader - gestisce solo codice sorgente."""

import re
from pathlib import Path


def converti(file_path: str | Path) -> str:
    """Converte un file HTML/CSS/JS in una stringa Markdown con codice minificato.

    Per i file di codice sorgente (HTML, CSS, JS):
    - Esegue una minificazione leggera (rimozione commenti e spaziature superflua)
    - Formatta l'output in blocchi di codice preformattati con linguaggio

    Args:
        file_path: Percorso al file HTML/CSS/JS.

    Returns:
        Stringa Markdown con il contenuto minificato formattato.

    Raises:
        IOError: Se il file non esiste o non è leggibile.
    """
    file_path = Path(file_path)

    # --- Validazione file ---
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")

    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as exc:
        raise IOError(
            f"Impossibile leggere il file: {file_path} — {exc}"
        ) from exc

    if not content.strip():
        return "*File vuoto o senza contenuti.*\n"

    # Determina il linguaggio basato sull'estensione
    language = _get_language_from_extension(file_path.suffix)
    
    # Minifica il contenuto
    minified_content = _minify_content(content, language)
    
    # Formatta in blocco di codice Markdown
    return f"# {file_path.name}\n\n```{language}\n{minified_content}\n```"


def _get_language_from_extension(extension: str) -> str:
    """Determina il linguaggio basato sull'estensione del file."""
    extension = extension.lower()
    language_map = {
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
    }
    return language_map.get(extension, "text")


def _minify_content(content: str, language: str) -> str:
    """Esegue una minificazione leggera del contenuto."""
    if language == "html":
        # Rimuovi commenti HTML
        content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        # Rimuovi spaziature superflue
        content = re.sub(r"\s+", " ", content)
        # Rimuovi spazi all'inizio e alla fine delle righe
        content = re.sub(r"^\s+|\s+$", "", content, flags=re.MULTILINE)
    elif language in ["css", "javascript", "typescript"]:
        # Rimuovi commenti CSS/JS/TS
        if language == "css":
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        else:
            content = re.sub(r"//.*?$|/\*.*?\*/", "", content, flags=re.MULTILINE | re.DOTALL)
        # Rimuovi spaziature superflua
        content = re.sub(r"\s+", " ", content)
        # Rimuovi spazi all'inizio e alla fine delle righe
        content = re.sub(r"^\s+|\s+$", "", content, flags=re.MULTILINE)
    
    # Rimuovi righe vuote multiple
    content = re.sub(r"\n\n+", "\n", content)
    
    return content.strip()
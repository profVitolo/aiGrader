"""Convertitore Archivi per AiGrader - decomprime e processa file archiviati."""

import zipfile
import tarfile
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Any
import shutil

from converters import get_converter


def _extract_archive(file_path: Path, temp_dir: Path) -> Path:
    """Estrae un archivio in una directory temporanea.

    Args:
        file_path: Percorso al file archivio.
        temp_dir: Directory temporanea dove estrarre.

    Returns:
        Path della directory di estrazione.
    """
    archive_path = Path(file_path)
    extract_dir = temp_dir / archive_path.stem

    # Crea la directory di estrazione
    extract_dir.mkdir(parents=True, exist_ok=True)

    if archive_path.suffix.lower() in ['.zip']:
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
    elif archive_path.suffix.lower() in ['.tar.gz', '.tgz']:
        with tarfile.open(archive_path, 'r:gz') as tar_ref:
            tar_ref.extractall(extract_dir)
    elif archive_path.suffix.lower() in ['.tar.bz2']:
        with tarfile.open(archive_path, 'r:bz2') as tar_ref:
            tar_ref.extractall(extract_dir)
    elif archive_path.suffix.lower() in ['.tar']:
        with tarfile.open(archive_path, 'r') as tar_ref:
            tar_ref.extractall(extract_dir)
    else:
        raise ValueError(f"Tipo di archivio non supportato: {archive_path.suffix}")

    return extract_dir


def _recursive_process_directory(directory: Path, results: List[str]) -> None:
    """Processa ricorsivamente una directory per trovare file supportati.

    Args:
        directory: Directory da processare.
        results: Lista per accumulare i risultati convertiti.
    """
    # Per ogni file nella directory
    for item in directory.iterdir():
        if item.is_file():
            # Verifica se il file è supportato
            converter = get_converter(item.suffix)
            if converter:
                try:
                    # Converte il file
                    converted_content = converter(item)
                    if converted_content:
                        # Aggiungi il contenuto al risultato con informazioni sulla struttura
                        results.append(f"## File: {item.name}\n\n{converted_content}\n")
                except Exception as e:
                    # Se la conversione fallisce, aggiungi un messaggio di errore
                    results.append(f"## File: {item.name}\n\n[Errore nella conversione: {str(e)}]\n")
        elif item.is_dir():
            # Processa ricorsivamente le sottodirectory
            _recursive_process_directory(item, results)


def converti(file_path: str | Path) -> str:
    """Converte un file archivio in una stringa Markdown.

    Estrae l'archivio in una directory temporanea, naviga l'albero dei file,
    invoca i convertitori appropriati per ogni file identificato e restituisce
    un unico stringone Markdown formattato.

    Args:
        file_path: Percorso al file archivio (.zip, .tar.gz, .tgz, .tar.bz2).

    Returns:
        Stringa Markdown con tutti i contenuti convertiti.

    Raises:
        IOError: Se il file non esiste o non è leggibile.
        ValueError: Se il tipo di archivio non è supportato.
    """
    file_path = Path(file_path)

    # --- Validazione file ---
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")

    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")

    # Crea una directory temporanea per l'estrazione
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        try:
            # Estrae l'archivio
            extract_dir = _extract_archive(file_path, temp_path)
            
            # Raccoglie i risultati
            results: List[str] = []
            
            # Processa ricorsivamente la directory estratta
            _recursive_process_directory(extract_dir, results)
            
            # Costruisce il risultato finale
            if not results:
                return "*Nessun file supportato trovato nell'archivio.*\n"
            
            # Aggiunge un'intestazione con informazioni sull'archivio
            md_parts = [f"# Archivio: {file_path.name}\n\n"]
            
            # Aggiunge la struttura dell'archivio
            md_parts.append("## Struttura dell'Archivio\n\n")
            md_parts.append(_get_directory_structure(extract_dir))
            md_parts.append("\n")
            
            # Aggiunge tutti i contenuti convertiti
            md_parts.extend(results)
            
            return "".join(md_parts)
            
        except Exception as e:
            raise IOError(f"Errore durante l'elaborazione dell'archivio {file_path}: {e}") from e


def _get_directory_structure(directory: Path, prefix: str = "") -> str:
    """Ottiene la struttura della directory in formato markdown.

    Args:
        directory: Directory da analizzare.
        prefix: Prefisso per la struttura (usato per la ricorsione).

    Returns:
        Stringa markdown con la struttura della directory.
    """
    items = sorted(directory.iterdir())
    if not items:
        return "*Directory vuota*\n"
    
    lines = []
    for i, item in enumerate(items):
        is_last = (i == len(items) - 1)
        connector = "└── " if is_last else "├── "
        
        if item.is_dir():
            lines.append(f"{prefix}{connector}{item.name}/")
            sub_prefix = prefix + ("    " if is_last else "│   ")
            lines.append(_get_directory_structure(item, sub_prefix))
        else:
            lines.append(f"{prefix}{connector}{item.name}")
    
    return "\n".join(lines)
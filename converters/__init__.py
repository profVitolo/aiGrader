import importlib
from config.settings import Settings


def get_extension(file_path) -> str:
    """Restituisce l'estensione 'utile' ai fini della mappa convertitori.

    Es. per 'foo.tar.gz' → '.tar.gz'; per 'foo.xlsx' → '.xlsx';
    per 'foo.txt' → '.txt' (fallback a Path.suffix).
    """
    from pathlib import Path
    name = Path(file_path).name.lower()
    # Prima le estensioni più lunghe, per evitare che '.gz' catturi '.tar.gz'
    for ext in sorted(Settings.CONVERTERS_MAP, key=len, reverse=True):
        if name.endswith(ext):
            return ext
    return Path(file_path).suffix.lower()
    

def is_supported(file_path) -> bool:
    """Ritorna True se esiste un convertitore per il file.

    Gestisce correttamente estensioni composte (.tar.gz, .tar.bz2),
    a differenza di un semplice confronto su Path.suffix.

    Args:
        file_path: Path o str del file.

    Returns:
        True se il file è convertibile, False altrimenti.
    """
    from pathlib import Path
    path = Path(file_path)
    name_lower = path.name.lower()

    # Match diretto per estensioni composte note
    for ext in Settings.CONVERTERS_MAP:
        if name_lower.endswith(ext):
            return True
    return False

def get_converter(extension: str):
    """
    Carica dinamicamente il convertitore in base all'estensione del file.
    Gestisce anche estensioni composte come .tar.gz e .tar.bz2.
    
    Args:
        extension: estensione del file (es. '.xlsx', '.docx', '.html', '.tar.gz')
    
    Returns:
        Funzione converti(file_path) del modulo convertitore, o None se non supportato
    """
    ext_lower = extension.lower()
    
    # Cerca prima l'estensione esatta
    module_path = Settings.CONVERTERS_MAP.get(ext_lower)
    
    # Se non trovato, prova con estensioni composte (es. .tar.gz, .tar.bz2)
    if not module_path and '.' in ext_lower:
        parts = ext_lower.split('.')
        if len(parts) >= 3:
            # Ricostruisce estensioni composte: .tar.gz, .tar.bz2
            for i in range(len(parts), 1, -1):
                compound = '.'.join(parts[-i:])
                compound_ext = f'.{compound}'
                module_path = Settings.CONVERTERS_MAP.get(compound_ext)
                if module_path:
                    break
    
    if not module_path:
        return None

    # Importa dinamicamente il modulo
    module = importlib.import_module(module_path)
    return module.converti

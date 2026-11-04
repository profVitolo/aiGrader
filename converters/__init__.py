import importlib
from config.settings import Settings


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

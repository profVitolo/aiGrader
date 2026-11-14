from pathlib import Path


class Settings:
    """Configurazione parametrica per AiGrader."""

    # LLM Configuration
    LLM_BASE_URL: str = "http://localhost:8080/v1"
    LLM_API_KEY: str = "sk-no-key-required"
    LLM_MODEL: str = "Q8_8-Qwen3.6-35B-A3B-UD-Q4_K_XL_MTP"  #"Qwen3.6-35B-A3B-UD-Q4_K_XL_MTP"   # "ISTA-DASLab-Qwen3.8-27B-IQ3_XSS-MTP"  # 
    LLM_TEMPERATURE: float = 0.0
    LLM_TOP_P: float = 1.0

    # Pivot Table XML rendering
    PIVOT_XML_MAX_LENGTH: int = 10000  # Max chars for raw XML in pivot table output

    # Soglia sotto la quale un docx è considerato "template vuoto" (Fase 3)
    DOCX_MIN_CONTENT_CHARS: int = 100

    # Converter mapping (extension -> module path)
    CONVERTERS_MAP: dict[str, str] = {
        ".xlsx": "converters.xlsx_converter",
        ".docx": "converters.docx_converter",
        ".doc": "converters.doc_converter",
        ".html": "converters.web_converter",
        ".htm": "converters.web_converter",
        ".css": "converters.web_converter",
        ".js": "converters.web_converter",
        # Archivi compressi
        ".zip": "converters.archive_converter",
        ".tar": "converters.archive_converter",
        ".tgz": "converters.archive_converter",
        ".tar.gz": "converters.archive_converter",
        ".tar.bz2": "converters.archive_converter",
        ".gz": "converters.archive_converter",
        ".bz2": "converters.archive_converter",
    }

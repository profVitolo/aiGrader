## Struttura delle cartelle del progetto

```
AiGrader/
│
├── README.md
├── SPECIFICATION.md
├── main.py                       # Script orchestratore principale
│
├── config/                       # Configurazione parametrica globale
│   ├── __init__.py
│   └── settings.py
│
├── prompts/                      # Template di prompt condivisi
│   ├── prompt_griglia.md
│   ├── prompt_correzione.md
│   └── prompt_riepilogo.md
│
├── utils/                        # Connessione LLM e health check
│   ├── __init__.py
│   └── llm_client.py
│
├── converters/                   # Moduli di parsing specifici per formato
│   ├── __init__.py
│   ├── xlsx_converter.py
│   ├── docx_converter.py
│   ├── web_converter.py
│   └── ...
│
└── elaborazioni/                 # Contenitore di tutte le verifiche scolastiche
    │
    ├── 2026_03_bilancio_excel/   # <--- Esempio: Compito 1 (Excel)
    │   ├── traccia.md            # Traccia specifica di questa prova
    │   ├── griglia_valutazione.md# Generata automaticamente per questo compito
    │   ├── riepilogo.md          # Riepilogo finale della classe per questo compito
    │   │
    │   ├── consegne/             # File grezzi degli studenti per questa prova
    │   │   ├── rossi mario - bilancio.xlsx
    │   │   └── bianchi giulia - bilancio.xlsx
    │   │
    │   └── correzioni/           # Report e griglie individuali generate dall'IA
    │       ├── rossi mario - griglia.md
    │       └── bianchi giulia - griglia.md
    │
    └── 2026_04_portfolio_web/    # <--- Esempio: Compito 2 (HTML/CSS futuro)
        ├── traccia.md
        ├── griglia_valutazione.md
        ├── riepilogo.md
        ├── consegne/
        │   ├── rossi mario - index.html
        │   └── ...
        └── correzioni/	
		└── ...
```
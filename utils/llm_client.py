from openai import OpenAI, APIConnectionError, APITimeoutError
from config.settings import Settings


def create_client() -> OpenAI:
    """Istanzia il client OpenAI con configurazione da config.settings."""
    return OpenAI(
        base_url=Settings.LLM_BASE_URL,
        api_key=Settings.LLM_API_KEY,
    )


def call_llm(messages: list[dict], model: str = None) -> str:
    """
    Invia messaggi all'LLM e restituisce la risposta.
    
    Args:
        messages: lista di dict con 'role' e 'content'
        model: nome modello (default: Settings.LLM_MODEL)
    
    Returns:
        Stringa con il contenuto della risposta
    
    Raises:
        Exception: con messaggio descrittivo in caso di errore di rete
    """
    client = create_client()
    model = model or Settings.LLM_MODEL
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=Settings.LLM_TEMPERATURE,
        )
        return response.choices[0].message.content
    except (APIConnectionError, APITimeoutError) as e:
        raise Exception(f"Errore di connessione LLM ({Settings.LLM_BASE_URL}): {e}")
    except Exception as e:
        raise Exception(f"Errore imprevisto durante la chiamata LLM: {e}")


def call_llm_with_system_prompt(system_prompt: str, user_prompt: str, model: str = None) -> str:
    """
    Helper per chiamate con system prompt separato.
    
    Args:
        system_prompt: istruzione di sistema
        user_prompt: messaggio dell'utente
        model: nome modello (default: Settings.LLM_MODEL)
    
    Returns:
        Stringa con il contenuto della risposta
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return call_llm(messages, model)


def health_check() -> bool:
    """
    Verifica se il LLM è raggiungibile.
    
    Returns:
        True se il LLM risponde, False altrimenti
    """
    try:
        client = create_client()
        # Tentativo minimo per verificare connettività
        client.models.list()
        return True
    except Exception:
        return False

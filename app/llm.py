"""
Thin wrapper around the LLM provider. Three jobs only:
  - generate_json(prompt, Schema) -> validated Pydantic object
  - embed(texts) -> list of vectors
  - a disk cache keyed by prompt hash, so identical prompts never re-spend money

We talk to Eden AI's OpenAI-compatible API through the openai SDK, so swapping providers
is a base_url + model-name change. Prompts live as Markdown files in prompts/ and are
filled with {{variables}} here, so the prompts/ directory is the real source of truth.
"""
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
CACHE_PATH = ROOT / "data" / "llm_cache.json"
load_dotenv(ROOT / ".env")

BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.edenai.run/v3")
MODEL = os.environ.get("LLM_MODEL", "openai/gpt-4.1-mini")            # fast, cheap, good at JSON
FALLBACKS = [m for m in os.environ.get("LLM_FALLBACKS", "google/gemini-2.5-flash,openai/gpt-4o-mini").split(",") if m]
EMBED_MODEL = os.environ.get("EMBED_MODEL", "openai/text-embedding-3-small")

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_client: OpenAI | None = None
_cache_data: dict[str, dict] | None = None
_cache_mtime: float = 0.0


def client() -> OpenAI:
    """Lazy singleton so importing this module never needs the key (tests, eval)."""
    global _client
    if _client is None:
        key = os.environ.get("EDEN_API_KEY")
        if not key:
            raise RuntimeError("EDEN_API_KEY is not set. Copy .env.example to .env and add your key.")
        _client = OpenAI(api_key=key, base_url=BASE_URL, timeout=60)
    return _client


def load_prompt(name: str, **variables: str) -> str:
    """Read prompts/<name>.md and substitute {{var}} placeholders.
    Plain replace (not str.format) so JSON braces inside prompts are safe."""
    text = (PROMPTS_DIR / f"{name}.md").read_text()
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text


# ---- cache -----------------------------------------------------------------

def cache_enabled() -> bool:
    return os.environ.get("LLM_CACHE", "1") != "0"


def _cache() -> dict[str, dict]:
    """In-memory copy of the cache file, re-read whenever another process (the eval script,
    a second server) has written to it, so nobody overwrites anybody else's entries."""
    global _cache_data, _cache_mtime
    mtime = CACHE_PATH.stat().st_mtime if CACHE_PATH.exists() else 0.0
    if _cache_data is None or mtime != _cache_mtime:
        _cache_data = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
        _cache_mtime = mtime
    return _cache_data


def _cache_key(prompt: str, schema: type[BaseModel], temperature: float) -> str:
    # The model is deliberately NOT part of the key: the cache stores "the answer to this prompt",
    # whichever model produced it.
    return hashlib.sha256(f"{schema.__name__}|{temperature}|{prompt}".encode()).hexdigest()[:24]


# ---- calls -----------------------------------------------------------------

def _with_retries(fn, attempts: int = 3):
    """Retry on rate limits / overload with a short backoff."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as ex:  # noqa: BLE001
            transient = any(code in str(ex) for code in ("429", "500", "502", "503", "rate", "overloaded", "timeout"))
            if not transient or i == attempts - 1:
                raise
            time.sleep(2 * 2 ** i)


def generate_json(prompt: str, schema: type[T], temperature: float = 0.3) -> T:
    """Ask the model for JSON matching a Pydantic schema and return the parsed object.
    Cached on disk by prompt hash; a cached answer that no longer fits the schema is refetched.
    If the model's JSON fails validation, it gets one repair round with the error message."""
    key = _cache_key(prompt, schema, temperature)
    if cache_enabled() and key in _cache():
        try:
            return schema.model_validate_json(_cache()[key]["response"])
        except ValidationError:
            pass  # schema changed since this was cached

    messages = [
        {"role": "system", "content": load_prompt("system").strip()},
        {"role": "user", "content": prompt},
    ]

    def call():
        return client().chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True, "schema": schema.model_json_schema()}},
            extra_body={"fallbacks": FALLBACKS} if FALLBACKS else None,   # Eden-specific: try these if MODEL fails
        )

    response = _with_retries(call)
    text = response.choices[0].message.content
    try:
        parsed = schema.model_validate_json(text)
    except ValidationError as err:
        log.warning("JSON failed validation, asking the model to repair it: %s", str(err)[:200])
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That JSON failed validation:\n{err}\nReturn the corrected JSON object only."}]
        response = _with_retries(call)
        parsed = schema.model_validate_json(response.choices[0].message.content)

    if cache_enabled():
        _cache()[key] = {"model": getattr(response, "model", MODEL), "schema": schema.__name__,
                         "prompt_head": prompt[:100], "response": parsed.model_dump_json()}
        CACHE_PATH.write_text(json.dumps(_cache(), indent=1))
        globals()["_cache_mtime"] = CACHE_PATH.stat().st_mtime
    return parsed


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings, in batches of 100."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 100):
        batch = texts[start:start + 100]
        result = _with_retries(lambda: client().embeddings.create(model=EMBED_MODEL, input=batch))
        vectors.extend(d.embedding for d in sorted(result.data, key=lambda d: d.index))
    return vectors

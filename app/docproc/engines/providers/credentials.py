"""
Provider credentials entered in the Settings screen, kept encrypted in the database.

Where a setting comes from, first match wins:  a value saved in the app  ->  the server's environment variable.
So a deployment can be configured entirely from the environment (nothing saved), entirely from the screen, or a mix; removing a
saved value falls back to the environment.

Storage
    Each value is encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before it is written to the `provider_credentials` table.
    The encryption key is APP_SECRET_KEY when the server sets one (recommended for production: keep it outside the data
    directory, so a copy of the data directory alone reveals nothing). Otherwise a random key is generated once and kept in
    `<DATA_DIR>/secret.key` with owner-only permissions.
    Values are never returned by the API (only "set", where it came from, and the last four characters of a key), never logged,
    and never written to extraction results.

LOCK_SETTINGS=1 makes the screen read-only for deployments where an operator manages configuration through the environment.
"""
import base64
import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.docproc import config

logger = logging.getLogger("app.docproc.engines.providers.credentials")

CACHE_SECONDS = 15
MAX_LENGTH = 512
_URL = re.compile(r"^https?://[^\s]+$", re.I)


@dataclass(frozen=True)
class Field:
    name: str                 # also the environment variable that sets the same thing
    label: str
    secret: bool = False      # a key: shown masked, never returned
    kind: str = "text"        # "key" | "url" | "text"
    help: str = ""


FIELDS: Dict[str, Tuple[Field, ...]] = {
    "openai": (Field("OPENAI_API_KEY", "API key", True, "key", "From platform.openai.com. Starts with sk-."),
               Field("OPENAI_BASE_URL", "Base URL (optional)", False, "url", "Only for a proxy or gateway that speaks the OpenAI API.")),
    "anthropic": (Field("ANTHROPIC_API_KEY", "API key", True, "key", "From console.anthropic.com. Starts with sk-ant-."),),
    "gemini": (Field("GEMINI_API_KEY", "API key", True, "key", "From aistudio.google.com."),),
    "ollama": (Field("OLLAMA_BASE_URL", "Server address", False, "url", "Where Ollama runs, for example http://localhost:11434."),),
    "custom": (Field("LLM_CUSTOM_BASE_URL", "Server address", False, "url", "An OpenAI-compatible endpoint, for example http://vllm:8000/v1."),
               Field("LLM_CUSTOM_API_KEY", "API key (if it needs one)", True, "key"),
               Field("LLM_CUSTOM_MODEL", "Default model name", False, "text", "Used when no model is chosen, for example my-vision-model.")),
}
_OWNER = {f.name: pid for pid, fs in FIELDS.items() for f in fs}
GEMINI_ALIAS = "GOOGLE_API_KEY"      # still honoured from the environment

_lock = threading.Lock()
_cache: Dict[str, Any] = {"stamp": None, "at": 0.0, "values": {}}


def locked() -> bool:
    return os.getenv("LOCK_SETTINGS", "0").lower() in ("1", "true", "yes")


class StorageError(Exception):
    """Credentials cannot be stored safely right now. The message is safe to show."""


# ---------------------------------------------------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------------------------------------------------

def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise StorageError("Saving keys needs the 'cryptography' package on the server. Set the key as an environment variable instead.") from e
    secret = os.getenv("APP_SECRET_KEY", "").strip()
    if secret:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
    path = config.data_dir() / "secret.key"
    try:
        if path.exists():
            return Fernet(path.read_bytes().strip())
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)    # owner-only from the first byte
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return Fernet(key)
    except OSError as e:
        raise StorageError("The server cannot keep an encryption key in its data folder. Set APP_SECRET_KEY, or set the key as an environment variable.") from e


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(token: str) -> Optional[str]:
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, StorageError):
        return None              # a different APP_SECRET_KEY than the one used to save it: treat as not set, never crash


# ---------------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------------

def _db_ready() -> bool:
    from app.docproc import db
    return db._Session is not None


def _saved() -> Dict[str, str]:
    """All saved values, decrypted, cached briefly (model calls ask for credentials constantly)."""
    stamp = (config.DB_URL, os.getenv("DOCPROC_DATA_DIR"))
    now = time.monotonic()
    with _lock:
        if _cache["stamp"] == stamp and now - _cache["at"] < CACHE_SECONDS:
            return _cache["values"]
    values: Dict[str, str] = {}
    if _db_ready():
        from app.docproc import db
        try:
            with db.session() as s:
                rows = s.query(db.ProviderCredential).all()
            for r in rows:
                v = _decrypt(r.value)
                if v:
                    values[r.name] = v
        except Exception:
            logger.exception("Could not read saved provider credentials")
    with _lock:
        _cache.update(stamp=stamp, at=now, values=values)
    return values


def invalidate() -> None:
    with _lock:
        _cache.update(stamp=None, at=0.0, values={})


def get(name: str) -> str:
    """The value in force for a setting: saved in the app, else the environment ('' when neither)."""
    saved = _saved().get(name)
    if saved:
        return saved.strip()
    env = (os.getenv(name) or "").strip()
    if not env and name == "GEMINI_API_KEY":
        env = (os.getenv(GEMINI_ALIAS) or "").strip()
    return env


def source(name: str) -> Optional[str]:
    if _saved().get(name):
        return "saved"
    return "environment" if get(name) else None


# ---------------------------------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------------------------------

def validate(name: str, value: str) -> str:
    """The cleaned value, or ValueError with a message safe to show."""
    f = next((f for fs in FIELDS.values() for f in fs if f.name == name), None)
    if f is None:
        raise ValueError("That setting does not exist.")
    v = (value or "").strip()
    if not v or len(v) > MAX_LENGTH or re.search(r"[\x00-\x1f\x7f]", v):
        raise ValueError(f"{f.label} is empty, too long, or contains characters that cannot be part of it.")
    if f.kind == "url" and not _URL.match(v):
        raise ValueError(f"{f.label} must be a web address starting with http:// or https://.")
    if f.kind == "key" and (len(v) < 8 or re.search(r"\s", v)):
        raise ValueError(f"That does not look like a complete {f.label.lower()}. Paste it exactly as the provider shows it.")
    return v


def save(values: Dict[str, Optional[str]]) -> None:
    """Set (a string) or remove (None) saved values. Raises ValueError for bad input, StorageError if they cannot be stored."""
    from app.docproc import db
    cleaned: Dict[str, Optional[str]] = {}
    for name, v in values.items():
        if name not in _OWNER:
            raise ValueError("That setting does not exist.")
        cleaned[name] = None if v is None else validate(name, v)
    if any(v is not None for v in cleaned.values()):
        _fernet()                                           # fail before touching the database if nothing can be encrypted
    with db.session() as s:
        for name, v in cleaned.items():
            row = s.get(db.ProviderCredential, name)
            if v is None:
                if row is not None:
                    s.delete(row)
            elif row is None:
                s.add(db.ProviderCredential(name=name, value=_encrypt(v)))
            else:
                row.value = _encrypt(v)
        s.commit()
    invalidate()


def preview(name: str) -> str:
    """What the screen may show about a value: a key as dots plus its last four characters, an address in full."""
    f = next((f for fs in FIELDS.values() for f in fs if f.name == name), None)
    v = get(name)
    if not v or f is None:
        return ""
    return ("••••••••" + v[-4:]) if f.secret and len(v) > 8 else ("••••••••" if f.secret else v)


def view(provider_id: str) -> List[Dict[str, Any]]:
    return [{"name": f.name, "label": f.label, "secret": f.secret, "kind": f.kind, "help": f.help, "set": bool(get(f.name)),
             "source": source(f.name), "preview": preview(f.name)} for f in FIELDS.get(provider_id, ())]


def encryption_ok() -> bool:
    try:
        _fernet()
        return True
    except StorageError:
        return False

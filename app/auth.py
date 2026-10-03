"""Anmeldung mit Passwort und signiertem Sitzungs-Cookie.

Warum Cookie statt Basic-Auth: Browser schicken Cookies zuverlässig auch beim
WebSocket-Aufbau mit (Live-Aufnahme), Basic-Auth-Daten dagegen nicht immer.
Das Cookie ist HttpOnly, SameSite=Strict und bei HTTPS „Secure“.
"""

from __future__ import annotations

import hashlib
import logging
import hmac
import secrets
import time
from collections import defaultdict

from .config import settings

COOKIE = "mitschrift_session"
_secret = (settings.session_secret or secrets.token_hex(32)).encode()
log = logging.getLogger(__name__)
_failures: dict[str, list[float]] = defaultdict(list)
_store = None   # für ein in der Anwendung geändertes Zugangspasswort


def set_store(store) -> None:
    global _store
    _store = store


def _override() -> dict | None:
    """In der Anwendung gesetztes Passwort (gehasht) – hat Vorrang vor ACCESS_PASSWORD aus der .env."""
    return _store.get_setting("zugang_passwort", None) if _store is not None else None


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def enabled() -> bool:
    return bool(settings.access_password) or bool(_override())


def _generation() -> str:
    o = _override()
    return o["salt"][:8] if o else "env"


def set_password(neu: str) -> None:
    """Neues Zugangspasswort speichern. Alle bestehenden Anmeldungen werden damit ungültig."""
    salt = secrets.token_hex(16)
    _store.set_setting("zugang_passwort", {"salt": salt, "hash": _hash(neu, salt), "geaendert": int(time.time())})


def make_token() -> str:
    exp = int(time.time()) + settings.session_hours * 3600
    sig = hmac.new(_secret, f"{exp}.{_generation()}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_token(token: str | None) -> bool:
    if not enabled():
        return True
    if not token or "." not in token:
        return False
    exp, _, sig = token.partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    expected = hmac.new(_secret, f"{exp}.{_generation()}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


_global_fails: list[float] = []   # Fehlversuche insgesamt (unabhängig von der Absenderadresse)
MAX_PER_CLIENT, MAX_GLOBAL, WINDOW = 10, 60, 900


def _prune(now: float) -> None:
    for k in [k for k, v in _failures.items() if not v or now - v[-1] > WINDOW]:
        _failures.pop(k, None)
    _global_fails[:] = [t for t in _global_fails if now - t < WINDOW]


def check_password(password: str, client: str) -> bool:
    """Passwort prüfen, mit Sperre gegen Durchprobieren: 10 Fehlversuche / 15 min je Absender und 60 insgesamt
    (Behörden-Proxys bündeln viele Nutzer hinter einer Adresse – deshalb je Absender großzügiger, insgesamt gedeckelt)."""
    now = time.time()
    _prune(now)
    fails = [t for t in _failures[client] if now - t < WINDOW]
    _failures[client] = fails
    if len(fails) >= MAX_PER_CLIENT or len(_global_fails) >= MAX_GLOBAL:
        return False
    o = _override()
    if o:
        ok = hmac.compare_digest(_hash(password, o["salt"]), o["hash"])
    else:
        ok = hmac.compare_digest(password.encode(), settings.access_password.encode())
    if not ok:
        fails.append(now)
        _global_fails.append(now)
        log.warning("Fehlgeschlagene Anmeldung von %s (%d/%d in 15 min)", client, len(fails), MAX_PER_CLIENT)
    else:
        _failures.pop(client, None)
    return ok


def locked(client: str) -> bool:
    now = time.time()
    _prune(now)
    return len([t for t in _failures.get(client, []) if now - t < WINDOW]) >= MAX_PER_CLIENT or len(_global_fails) >= MAX_GLOBAL

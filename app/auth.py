"""Anmeldung mit Passwort und signiertem Sitzungs-Cookie.

Warum Cookie statt Basic-Auth: Browser schicken Cookies zuverlässig auch beim
WebSocket-Aufbau mit (Live-Aufnahme), Basic-Auth-Daten dagegen nicht immer.
Das Cookie ist HttpOnly, SameSite=Strict und bei HTTPS „Secure“.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict

from .config import settings

COOKIE = "mitschrift_session"
_secret = (settings.session_secret or secrets.token_hex(32)).encode()
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


def check_password(password: str, client: str) -> bool:
    """Passwort prüfen, mit einfacher Sperre gegen Durchprobieren (5 Fehlversuche / 15 min je IP)."""
    now = time.time()
    fails = [t for t in _failures[client] if now - t < 900]
    _failures[client] = fails
    if len(fails) >= 5:
        return False
    o = _override()
    if o:
        ok = hmac.compare_digest(_hash(password, o["salt"]), o["hash"])
    else:
        ok = hmac.compare_digest(password.encode(), settings.access_password.encode())
    if not ok:
        fails.append(now)
    else:
        _failures.pop(client, None)
    return ok


def locked(client: str) -> bool:
    now = time.time()
    return len([t for t in _failures.get(client, []) if now - t < 900]) >= 5

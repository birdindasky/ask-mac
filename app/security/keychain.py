"""macOS Keychain wrapper for API keys.

Production (.app bundle): SecKeychain via pyobjc. macOS prompts user the
first time the app accesses the keychain item, allows forever after.

Dev (`python run.py`): fall back to a JSON file under DATA_DIR/secrets.json
when Keychain is unavailable or ALLOW_KEYCHAIN_FALLBACK is true. Never used
inside packaged builds (settings.ALLOW_KEYCHAIN_FALLBACK is False there).

API: get_secret(account) -> str | None, set_secret(account, value),
     delete_secret(account). All values are utf-8 strings (API keys).
"""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path

from .. import settings as _settings  # late-resolved, reload-friendly

_lock = threading.Lock()


def _fallback_file() -> Path:
    return _settings.DATA_DIR / "secrets.json"


def _keychain_service() -> str:
    return _settings.KEYCHAIN_SERVICE


# ---- Detection ----
def is_keychain_available() -> bool:
    try:
        import Security  # noqa: F401  (pyobjc-framework-Security)
        return True
    except ImportError:
        return False


# ---- Keychain backend ----
def _kc_get(account: str) -> str | None:
    import Security
    import Foundation

    query = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: _keychain_service(),
        Security.kSecAttrAccount: account,
        Security.kSecReturnData: True,
        Security.kSecMatchLimit: Security.kSecMatchLimitOne,
    }
    err, ref = Security.SecItemCopyMatching(query, None)
    if err == 0 and ref is not None:
        try:
            data = bytes(ref)
        except TypeError:
            # ref is NSData
            data = bytes(Foundation.NSData.dataWithData_(ref))
        return data.decode("utf-8", errors="replace")
    return None


def _kc_set(account: str, value: str) -> None:
    import Security
    import Foundation

    data = Foundation.NSData.dataWithBytes_length_(value.encode("utf-8"), len(value.encode("utf-8")))
    base = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: _keychain_service(),
        Security.kSecAttrAccount: account,
    }

    # Try update first; if not found, add.
    update_attrs = {Security.kSecValueData: data}
    err = Security.SecItemUpdate(base, update_attrs)
    if err != 0:
        # errSecItemNotFound = -25300
        add = {**base, Security.kSecValueData: data}
        err = Security.SecItemAdd(add, None)
        if err != 0:
            raise RuntimeError(f"Keychain SecItemAdd failed: {err}")


def _kc_delete(account: str) -> None:
    import Security

    query = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: _keychain_service(),
        Security.kSecAttrAccount: account,
    }
    Security.SecItemDelete(query)


# ---- JSON fallback ----
def _fb_load() -> dict:
    fb = _fallback_file()
    if not fb.exists():
        return {}
    try:
        return json.loads(fb.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _fb_save(data: dict) -> None:
    fb = _fallback_file()
    fb.parent.mkdir(parents=True, exist_ok=True)
    tmp = fb.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, fb)
    try:
        os.chmod(fb, 0o600)
    except OSError:
        pass


def _use_keychain() -> bool:
    """Use real Keychain when available AND we're not explicitly in fallback mode."""
    if not is_keychain_available():
        return False
    if _settings.ALLOW_KEYCHAIN_FALLBACK and os.environ.get("MLC_FORCE_KEYCHAIN") != "1":
        # Dev mode: skip keychain to avoid permission popups while iterating.
        return False
    return True


# ---- Public API ----
def get_secret(account: str) -> str | None:
    if not account:
        return None
    with _lock:
        if _use_keychain():
            try:
                return _kc_get(account)
            except Exception:
                return _fb_load().get(account)
        return _fb_load().get(account)


def set_secret(account: str, value: str) -> None:
    if not account:
        raise ValueError("account is empty")
    with _lock:
        if _use_keychain():
            try:
                _kc_set(account, value)
                return
            except Exception:
                pass
        data = _fb_load()
        data[account] = value
        _fb_save(data)


def delete_secret(account: str) -> None:
    if not account:
        return
    with _lock:
        if _use_keychain():
            try:
                _kc_delete(account)
            except Exception:
                pass
        data = _fb_load()
        if account in data:
            del data[account]
            _fb_save(data)


class KeychainStore:
    """Convenience namespacing wrapper. e.g. KeychainStore('provider').get(pid)."""

    def __init__(self, namespace: str):
        self.ns = namespace.strip(":")

    def _key(self, account: str) -> str:
        return f"{self.ns}:{account}"

    def get(self, account: str) -> str | None:
        return get_secret(self._key(account))

    def set(self, account: str, value: str) -> None:
        set_secret(self._key(account), value)

    def delete(self, account: str) -> None:
        delete_secret(self._key(account))

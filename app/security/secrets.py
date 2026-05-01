"""Domain helpers over the Keychain wrapper.

Keys are namespaced by domain:provider_id so each provider's secret is
addressable independently. Public surface: get_/set_/delete_provider_key
and the search-backend equivalents. All values are utf-8 strings.
"""
from __future__ import annotations

from .keychain import KeychainStore

_provider_store = KeychainStore("provider")
_search_store = KeychainStore("search")


def get_provider_key(provider_id: str) -> str:
    if not provider_id:
        return ""
    return _provider_store.get(provider_id) or ""


def set_provider_key(provider_id: str, key: str) -> None:
    if not provider_id:
        return
    _provider_store.set(provider_id, key or "")


def delete_provider_key(provider_id: str) -> None:
    if not provider_id:
        return
    _provider_store.delete(provider_id)


def get_search_key(backend_name: str) -> str:
    if not backend_name:
        return ""
    return _search_store.get(backend_name) or ""


def set_search_key(backend_name: str, key: str) -> None:
    if not backend_name:
        return
    _search_store.set(backend_name, key or "")


def delete_search_key(backend_name: str) -> None:
    if not backend_name:
        return
    _search_store.delete(backend_name)


def has_provider_key(provider_id: str) -> bool:
    return bool(get_provider_key(provider_id))


def has_search_key(backend_name: str) -> bool:
    return bool(get_search_key(backend_name))

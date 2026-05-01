"""Secret storage — Keychain in production, JSON fallback in dev."""
from .keychain import KeychainStore, get_secret, set_secret, delete_secret, is_keychain_available
from .secrets import (
    delete_provider_key,
    delete_search_key,
    get_provider_key,
    get_search_key,
    has_provider_key,
    has_search_key,
    set_provider_key,
    set_search_key,
)

__all__ = [
    "KeychainStore",
    "get_secret",
    "set_secret",
    "delete_secret",
    "is_keychain_available",
    "get_provider_key",
    "set_provider_key",
    "delete_provider_key",
    "has_provider_key",
    "get_search_key",
    "set_search_key",
    "delete_search_key",
    "has_search_key",
]

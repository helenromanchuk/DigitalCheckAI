"""Keep tests away from personal credentials and the real operating system keychain."""
from unittest.mock import Mock

import pytest
from keyring.backend import KeyringBackend

from app import config


@pytest.fixture(autouse=True)
def credential_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    store = Mock(spec=KeyringBackend)
    store.get_password.return_value = None

    def save(service, account, value):
        store.get_password.return_value = value

    store.set_password.side_effect = save
    monkeypatch.setattr(config, "_credential_store", lambda: store)
    return store

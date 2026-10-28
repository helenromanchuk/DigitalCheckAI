"""Regression coverage for secure Gemini key persistence and legacy migration."""
import os
import sys
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.routes import get_db
from app.main import app

# Keep the real factory for selection tests; the autouse fixture replaces it for all I/O.
_native_credential_store = config._credential_store


def test_save_uses_credential_store_without_creating_plaintext_file(credential_store):
    config.save_gemini_api_key("  test-gemini-key  ")

    credential_store.set_password.assert_called_once_with("DigitalCheckAI", "GEMINI_API_KEY", "test-gemini-key")
    assert config.get_gemini_api_key() == "test-gemini-key"
    assert not config.ENV_PATH.exists()
    assert "GEMINI_API_KEY" not in os.environ


def test_saved_key_takes_precedence_over_environment(credential_store, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "injected-key")
    config.save_gemini_api_key("saved-key")
    assert config.get_gemini_api_key() == "saved-key"
    assert os.environ["GEMINI_API_KEY"] == "injected-key"


def test_injected_environment_key_is_never_persisted(credential_store, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "injected-key")
    assert config.get_gemini_api_key() == "injected-key"
    credential_store.set_password.assert_not_called()
    assert not config.ENV_PATH.exists()


def test_legacy_key_migrates_and_other_settings_are_preserved(credential_store):
    config.ENV_PATH.write_text(
        '# Local settings\nGEMINI_MODEL=custom-model\nexport GEMINI_API_KEY = "old-key"\nPORT=8000\n',
        encoding="utf-8",
    )

    assert config.get_gemini_api_key() == "old-key"
    credential_store.set_password.assert_called_once_with("DigitalCheckAI", "GEMINI_API_KEY", "old-key")
    assert config.ENV_PATH.read_text(encoding="utf-8") == (
        "# Local settings\nGEMINI_MODEL=custom-model\nPORT=8000\n"
    )
    assert config.get_gemini_api_key() == "old-key"
    assert credential_store.set_password.call_count == 1


def test_save_removes_all_legacy_assignments_including_multiline(credential_store):
    config.ENV_PATH.write_text(
        'GEMINI_API_KEY=old-one\nexport GEMINI_API_KEY="old\nmultiline-key"\nPORT=8000\n',
        encoding="utf-8",
    )
    config.save_gemini_api_key("replacement-key")
    assert config.ENV_PATH.read_text(encoding="utf-8") == "PORT=8000\n"
    assert config.get_gemini_api_key() == "replacement-key"


def test_legacy_key_cannot_overwrite_saved_key(credential_store):
    credential_store.get_password.return_value = "saved-key"
    config.ENV_PATH.write_text("GEMINI_API_KEY=stale-key\n", encoding="utf-8")
    assert config.get_gemini_api_key() == "saved-key"
    credential_store.set_password.assert_not_called()
    assert "GEMINI_API_KEY" not in config.ENV_PATH.read_text(encoding="utf-8")


def test_placeholder_is_removed_without_storing_it(credential_store):
    config.ENV_PATH.write_text("GEMINI_API_KEY=your_gemini_api_key_here\n", encoding="utf-8")
    assert config.get_gemini_api_key() == ""
    credential_store.set_password.assert_not_called()
    assert config.ENV_PATH.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("has_legacy", [False, True])
def test_save_failure_does_not_write_plaintext_or_delete_legacy(credential_store, has_legacy, caplog):
    if has_legacy:
        config.ENV_PATH.write_text("GEMINI_API_KEY=old-key\nPORT=8000\n", encoding="utf-8")
    credential_store.set_password.side_effect = RuntimeError("backend failure containing submitted-secret")

    with pytest.raises(config.CredentialStoreError) as exc_info:
        config.save_gemini_api_key("submitted-secret")

    assert "submitted-secret" not in str(exc_info.value)
    assert "submitted-secret" not in caplog.text
    assert "GEMINI_API_KEY" not in os.environ
    if has_legacy:
        assert config.ENV_PATH.read_text(encoding="utf-8") == "GEMINI_API_KEY=old-key\nPORT=8000\n"
    else:
        assert not config.ENV_PATH.exists()


def test_unavailable_store_does_not_discard_legacy_key(credential_store):
    credential_store.get_password.side_effect = RuntimeError("locked")
    config.ENV_PATH.write_text("GEMINI_API_KEY=old-key\n", encoding="utf-8")
    with pytest.raises(config.CredentialStoreError):
        config.get_gemini_api_key()
    assert config.ENV_PATH.read_text(encoding="utf-8") == "GEMINI_API_KEY=old-key\n"


@pytest.mark.parametrize("environment_key", ["", "injected-key"])
def test_unavailable_store_supports_headless_use(credential_store, monkeypatch, environment_key):
    credential_store.get_password.side_effect = RuntimeError("no desktop keychain")
    monkeypatch.setenv("GEMINI_API_KEY", environment_key)
    assert config.get_gemini_api_key() == environment_key
    credential_store.set_password.assert_not_called()


def test_cleanup_failure_is_reported_after_secure_write(credential_store, monkeypatch):
    config.ENV_PATH.write_text("GEMINI_API_KEY=old-key\n", encoding="utf-8")
    monkeypatch.setattr(config, "unset_key", Mock(side_effect=PermissionError("not writable")))
    with pytest.raises(config.CredentialStoreError):
        config.save_gemini_api_key("replacement-key")
    credential_store.set_password.assert_called_once_with("DigitalCheckAI", "GEMINI_API_KEY", "replacement-key")
    assert config.ENV_PATH.read_text(encoding="utf-8") == "GEMINI_API_KEY=old-key\n"


def test_blank_key_is_rejected_without_changing_credentials(credential_store):
    with pytest.raises(ValueError):
        config.save_gemini_api_key("  ")
    credential_store.set_password.assert_not_called()


def test_native_factory_bypasses_configured_keyring(monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, "get_keyring", Mock(side_effect=AssertionError("plugin selection must not run")))
    store = _native_credential_store()
    expected_module = {
        "win32": "keyring.backends.Windows",
        "darwin": "keyring.backends.macOS",
        "linux": "keyring.backends.SecretService",
    }[sys.platform]
    assert type(store).__module__ == expected_module


def test_unsupported_platform_cannot_fall_back_to_plaintext(monkeypatch):
    monkeypatch.setattr(sys, "platform", "unsupported")
    with pytest.raises(config.CredentialStoreError):
        _native_credential_store()


@pytest.fixture
def client():
    db = Mock()
    db.get_pending_items_for_gemini.return_value = []
    overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_api_saves_securely_and_reports_only_status(client, credential_store):
    response = client.post("/api/config/gemini-key", json={"api_key": "submitted-secret"})
    assert response.status_code == 200
    assert response.json()["has_gemini_key"] is True
    assert "submitted-secret" not in response.text
    assert not config.ENV_PATH.exists()
    credential_store.set_password.assert_called_once_with("DigitalCheckAI", "GEMINI_API_KEY", "submitted-secret")


def test_api_save_failure_is_safe_and_does_not_classify(client, credential_store, caplog):
    credential_store.set_password.side_effect = RuntimeError("backend echoed submitted-secret")
    response = client.post("/api/config/gemini-key", json={"api_key": "submitted-secret"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credential_store_unavailable"
    assert "submitted-secret" not in response.text + caplog.text
    assert not config.ENV_PATH.exists()
    app.dependency_overrides[get_db]().get_pending_items_for_gemini.assert_not_called()


def test_config_api_reports_legacy_migration_failure_safely(client, credential_store):
    credential_store.get_password.side_effect = RuntimeError("backend echoed old-key")
    config.ENV_PATH.write_text("GEMINI_API_KEY=old-key\n", encoding="utf-8")
    response = client.get("/api/config")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credential_store_unavailable"
    assert "old-key" not in response.text

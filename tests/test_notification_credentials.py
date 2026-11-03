from cryptography.fernet import Fernet

from config.settings import settings
from notifications.credentials import decrypt_credential, encrypt_credential


def test_credentials_use_plural_encryption_key_setting(monkeypatch):
    monkeypatch.setattr(
        settings,
        "_values",
        {"NOTIFICATIONS_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode()},
    )

    encrypted = encrypt_credential("telegram-token")

    assert decrypt_credential(encrypted) == "telegram-token"

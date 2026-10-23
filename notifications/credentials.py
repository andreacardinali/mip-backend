from cryptography.fernet import Fernet, InvalidToken

from config.settings import settings

SETTING_NAME = "NOTIFICATION_CREDENTIAL_ENCRYPTION_KEY"


class NotificationCredentialError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = str(settings.get(SETTING_NAME, "") or "").strip()
    if not key:
        raise NotificationCredentialError(
            f"{SETTING_NAME} is required to store notification credentials"
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise NotificationCredentialError(
            f"{SETTING_NAME} is not a valid Fernet key"
        ) from exc


def encrypt_credential(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise NotificationCredentialError("Notification credential cannot be empty")
    return _fernet().encrypt(normalized.encode("utf-8")).decode("ascii")


def decrypt_credential(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise NotificationCredentialError(
            "Notification credential cannot be decrypted with the configured key"
        ) from exc

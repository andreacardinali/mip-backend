import os
from pathlib import Path

from dotenv import dotenv_values


_DEFAULTS = {
    "DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
}


class Settings:
    def __init__(self):
        env_file = Path(__file__).resolve().parent.parent / ".env"
        self._values = {}

        if env_file.exists():
            self._values.update(dotenv_values(env_file))

        self._values.update({k: v for k, v in os.environ.items() if v is not None})

    def get(
        self,
        key: str,
        default=None,
    ):
        if key in self._values:
            return self._values[key]
        if key in _DEFAULTS:
            return _DEFAULTS[key]
        return default

    def __getitem__(
        self,
        key: str,
    ):
        if key in self._values:
            return self._values[key]
        if key in _DEFAULTS:
            return _DEFAULTS[key]
        raise KeyError(key)

    def __getattr__(
        self,
        key: str,
    ):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(f"Setting '{key}' not found") from exc


settings = Settings()

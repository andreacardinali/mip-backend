from pathlib import Path

from dotenv import dotenv_values


class Settings:

    def __init__(self):

        env_file = Path(__file__).resolve().parent.parent / ".env"

        self._values = dict(dotenv_values(env_file))

    def get(
        self,
        key: str,
        default=None,
    ):
        return self._values.get(
            key,
            default,
        )

    def __getitem__(
        self,
        key: str,
    ):
        return self._values[key]

    def __getattr__(
        self,
        key: str,
    ):
        try:
            return self._values[key]

        except KeyError as exc:

            raise AttributeError(f"Setting '{key}' not found") from exc


settings = Settings()

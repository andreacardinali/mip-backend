from dataclasses import dataclass
from typing import Protocol


class SettingsReader(Protocol):
    def get(self, key: str, default=None): ...


SUPPORTED_PROVIDERS = {
    "gemini",
    "ollama",
    "openai_compatible",
}


@dataclass(frozen=True, slots=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""
    timeout: float = 60.0
    max_retries: int = 4

    @classmethod
    def from_settings(
        cls,
        settings: SettingsReader,
        *,
        prefix: str,
        default_provider: str,
        default_timeout: float,
        default_max_retries: int = 4,
    ) -> "LLMConfig":
        normalized_prefix = prefix.strip().upper().rstrip("_")
        config = cls(
            provider=str(
                settings.get(
                    f"{normalized_prefix}_PROVIDER",
                    default_provider,
                )
            )
            .strip()
            .lower(),
            base_url=str(settings.get(f"{normalized_prefix}_BASE_URL", "") or "")
            .strip()
            .rstrip("/"),
            api_key=str(settings.get(f"{normalized_prefix}_API_KEY", "") or "").strip(),
            model=str(settings.get(f"{normalized_prefix}_MODEL", "") or "").strip(),
            timeout=float(
                settings.get(
                    f"{normalized_prefix}_TIMEOUT",
                    default_timeout,
                )
            ),
            max_retries=int(
                settings.get(
                    f"{normalized_prefix}_MAX_RETRIES",
                    default_max_retries,
                )
            ),
        )
        config.validate(prefix=normalized_prefix)
        return config

    def validate(self, *, prefix: str = "LLM") -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported {prefix}_PROVIDER: {self.provider}")
        if not self.model:
            raise ValueError(f"Missing {prefix}_MODEL")
        if self.provider in {"ollama", "openai_compatible"} and not self.base_url:
            raise ValueError(f"Missing {prefix}_BASE_URL")
        if self.provider in {"gemini", "openai_compatible"} and not self.api_key:
            raise ValueError(f"Missing {prefix}_API_KEY")
        if self.timeout <= 0:
            raise ValueError(f"{prefix}_TIMEOUT must be > 0")
        if self.max_retries < 0:
            raise ValueError(f"{prefix}_MAX_RETRIES must be >= 0")

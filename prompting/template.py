from dataclasses import dataclass, field
from typing import Any

from llm.fingerprint import fingerprint


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    content: str
    response_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return fingerprint(
            {
                "name": self.name,
                "content": self.content,
                "response_schema": self.response_schema,
                "metadata": self.metadata,
            }
        )

    def render(self, **values: object) -> str:
        try:
            return self.content.format(**values)
        except KeyError as exc:
            missing = exc.args[0]
            raise ValueError(
                f"Prompt {self.name!r} is missing render value {missing!r}"
            ) from exc
        except (IndexError, ValueError) as exc:
            raise ValueError(
                f"Prompt {self.name!r} could not be rendered: {exc}"
            ) from exc

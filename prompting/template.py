from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    content: str

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

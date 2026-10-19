from pathlib import Path, PurePosixPath

from prompting.template import PromptTemplate


class PromptNotFoundError(FileNotFoundError):
    pass


class FileSystemPromptRepository:
    def __init__(self, root: str | Path, *, encoding: str = "utf-8") -> None:
        self.root = Path(root).resolve()
        self.encoding = encoding
        self._cache: dict[str, PromptTemplate] = {}

    def get(self, name: str) -> PromptTemplate:
        normalized = self._normalize_name(name)
        cached = self._cache.get(normalized)
        if cached is not None:
            return cached

        path = (self.root / normalized).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Prompt path escapes repository root: {name!r}")
        if not path.is_file():
            raise PromptNotFoundError(
                f"Prompt {normalized!r} was not found under {self.root}"
            )

        template = PromptTemplate(
            name=normalized,
            content=path.read_text(encoding=self.encoding),
        )
        self._cache[normalized] = template
        return template

    def render(self, prompt_name: str, **values: object) -> str:
        return self.get(prompt_name).render(**values)

    def clear_cache(self) -> None:
        self._cache.clear()

    @staticmethod
    def _normalize_name(name: str) -> str:
        candidate = str(name or "").strip().replace("\\", "/")
        if not candidate:
            raise ValueError("Prompt name cannot be empty")
        path = PurePosixPath(candidate)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Invalid prompt name: {name!r}")
        if path.suffix == "":
            path = path.with_suffix(".txt")
        return path.as_posix()

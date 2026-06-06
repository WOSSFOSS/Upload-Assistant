from typing import Any

__all__ = ["SearchRunner"]


def __getattr__(name: str) -> Any:
    if name == "SearchRunner":
        from src.search.runner import SearchRunner

        return SearchRunner
    raise AttributeError(name)

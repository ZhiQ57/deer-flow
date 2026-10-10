"""VSSAG prompt resources and lightweight template rendering."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files

_PLACEHOLDER = re.compile(r"\{\{\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    """Load a Markdown prompt packaged with VSSAG."""

    return files(__name__).joinpath(name).read_text(encoding="utf-8")


def render_prompt(name: str, **values: object) -> str:
    """Render the simple ``{{ name }}`` placeholders used by prompt files."""

    template = load_prompt(name)
    return _PLACEHOLDER.sub(
        lambda match: str(values.get(match.group("name"), match.group(0))),
        template,
    )


__all__ = ["load_prompt", "render_prompt"]

"""Hooks package.

Avoid importing hook modules here so `python -m hooks.pre_tool_use` does not
preload the target module and trigger runpy warnings.
"""

__all__: list[str] = []

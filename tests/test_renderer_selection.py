from __future__ import annotations

import pytest

from qa.gradio_renderer import GradioQARenderer
from qa.renderer_selection import select_renderer


def test_select_renderer_requires_gradio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("qa.renderer_selection.gradio_available", lambda: False)
    with pytest.raises(RuntimeError, match="Gradio is required"):
        select_renderer("plain_english")


def test_select_renderer_uses_gradio_for_plain_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("qa.renderer_selection.gradio_available", lambda: True)
    renderer = select_renderer("plain_english")
    assert isinstance(renderer, GradioQARenderer)


def test_select_renderer_uses_gradio_for_true_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("qa.renderer_selection.gradio_available", lambda: True)
    renderer = select_renderer("true_false")
    assert isinstance(renderer, GradioQARenderer)

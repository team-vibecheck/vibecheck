from __future__ import annotations

from qa.renderer_selection import select_renderer
from qa.sidecar import SidecarClient


def test_select_renderer_returns_sidecar_client() -> None:
    renderer = select_renderer("plain_english")
    assert isinstance(renderer, SidecarClient)


def test_select_renderer_returns_sidecar_for_true_false() -> None:
    renderer = select_renderer("true_false")
    assert isinstance(renderer, SidecarClient)


def test_select_renderer_returns_sidecar_for_faded_example() -> None:
    renderer = select_renderer("faded_example")
    assert isinstance(renderer, SidecarClient)

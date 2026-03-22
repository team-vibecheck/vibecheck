from __future__ import annotations

from unittest.mock import mock_open, patch

from qa.gradio_renderer import _is_wsl


def test_is_wsl_true_when_env_present(monkeypatch) -> None:
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert _is_wsl() is True


def test_is_wsl_true_when_proc_version_mentions_microsoft(monkeypatch) -> None:
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr("qa.gradio_renderer.sys.platform", "linux")
    with patch("builtins.open", mock_open(read_data="Linux version ... Microsoft ...")):
        assert _is_wsl() is True


def test_is_wsl_false_on_non_linux(monkeypatch) -> None:
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr("qa.gradio_renderer.sys.platform", "darwin")
    assert _is_wsl() is False

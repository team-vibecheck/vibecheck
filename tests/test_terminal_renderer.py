"""Tests for TerminalQARenderer UX behavior."""

from __future__ import annotations

from unittest.mock import patch

from core.models import QAPacket
from qa.terminal_renderer import TerminalQARenderer


def _make_packet(question_type: str = "plain_english") -> QAPacket:
    return QAPacket(
        question_type=question_type,  # type: ignore[arg-type]
        prompt_seed="Test seed",
        context_excerpt="test context",
    )


def test_header_shows_attempt_number_and_max() -> None:
    renderer = TerminalQARenderer(max_attempts=3)
    header = renderer._format_header(2, _make_packet())
    assert "2/3" in header
    assert "Plain English" in header


def test_faded_example_shows_fill_in_blanks() -> None:
    renderer = TerminalQARenderer()
    body = renderer._format_body("complete this code:", _make_packet("faded_example"))
    assert "Fill in the blanks" in body


def test_true_false_shows_answer_instruction() -> None:
    renderer = TerminalQARenderer()
    body = renderer._format_body("Is this safe?", _make_packet("true_false"))
    assert "True or False" in body


def test_eof_returns_empty_string() -> None:
    renderer = TerminalQARenderer()
    with patch.object(renderer, "_read_answer", return_value=""):
        answer = renderer.ask("question?", 1, _make_packet())
    assert answer == ""


def test_show_feedback_pass(capsys) -> None:
    renderer = TerminalQARenderer()
    renderer.show_feedback("", passed=True)
    # Output goes to stderr
    captured = capsys.readouterr()
    assert "Correct" in captured.err


def test_show_feedback_fail(capsys) -> None:
    renderer = TerminalQARenderer()
    renderer.show_feedback("Be more specific.", passed=False)
    captured = capsys.readouterr()
    assert "Not quite" in captured.err
    assert "Be more specific" in captured.err


def test_show_outcome_pass(capsys) -> None:
    renderer = TerminalQARenderer()
    renderer.show_outcome(passed=True, attempt_count=1)
    captured = capsys.readouterr()
    assert "PASSED" in captured.err
    assert "1/" in captured.err


def test_show_outcome_fail(capsys) -> None:
    renderer = TerminalQARenderer()
    renderer.show_outcome(passed=False, attempt_count=3)
    captured = capsys.readouterr()
    assert "FAILED" in captured.err
    assert "penalty" in captured.err.lower()

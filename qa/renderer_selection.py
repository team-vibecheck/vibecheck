from __future__ import annotations

from core.models import QuestionType
from qa.gradio_renderer import GradioQARenderer, gradio_available
from qa.terminal_renderer import TerminalQARenderer


def select_renderer(
    question_type: QuestionType,
    *,
    max_attempts: int = 3,
) -> TerminalQARenderer | GradioQARenderer:
    if question_type == "faded_example" and gradio_available():
        return GradioQARenderer(max_attempts=max_attempts)
    return TerminalQARenderer(max_attempts=max_attempts)

from __future__ import annotations

from core.models import QuestionType
from qa.gradio_renderer import GradioQARenderer, gradio_available


def select_renderer(
    question_type: QuestionType,
    *,
    max_attempts: int = 3,
) -> GradioQARenderer:
    del question_type
    if not gradio_available():
        raise RuntimeError(
            "Gradio is required for QA rendering. Install with: uv pip install 'vibecheck[ui]'"
        )
    return GradioQARenderer(max_attempts=max_attempts)

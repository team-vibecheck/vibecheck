from __future__ import annotations

from qa.sidecar import SidecarClient


def select_renderer(
    question_type: str,
    *,
    max_attempts: int = 3,
) -> SidecarClient:
    del question_type
    del max_attempts
    return SidecarClient()

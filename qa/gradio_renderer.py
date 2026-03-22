"""Gradio-backed QA renderer for VibeCheck.

Provides a browser-based code editor UI for faded_example questions.
Falls back gracefully when gradio is not installed.
"""

from __future__ import annotations

import contextlib
import importlib.util
import queue
import sys
import threading
from typing import Any

from core.models import QAPacket

_TIMEOUT_SECONDS = 300  # 5 minutes


def gradio_available() -> bool:
    return importlib.util.find_spec("gradio") is not None


class GradioQARenderer:
    """Blocking QA renderer that launches a local Gradio app for each question."""

    def __init__(self, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts

    def ask(self, question: str, attempt_number: int, packet: QAPacket) -> str:
        try:
            import gradio as gr
        except ImportError as exc:
            raise RuntimeError(
                "Gradio is not installed. Install with: uv pip install 'vibecheck[ui]'"
            ) from exc

        result_q: queue.Queue[str] = queue.Queue()
        app = self._build_app(gr, question, attempt_number, packet, result_q)

        server_thread = threading.Thread(
            target=self._launch_app,
            args=(app,),
            daemon=True,
        )
        server_thread.start()

        try:
            answer = result_q.get(timeout=_TIMEOUT_SECONDS)
        except queue.Empty:
            answer = ""
        finally:
            self._close_app(app)

        return answer

    def show_feedback(self, feedback: str, *, passed: bool) -> None:
        status = "Correct" if passed else f"Not quite. {feedback}"
        print(f"\n  {'✓' if passed else '✗'} {status}\n", file=sys.stderr)

    def show_outcome(self, *, passed: bool, attempt_count: int) -> None:
        if passed:
            print(
                f"\n  PASSED on attempt {attempt_count}/{self.max_attempts}. "
                f"Mutation will proceed.\n",
                file=sys.stderr,
            )
        else:
            print(
                f"\n  FAILED after {attempt_count}/{self.max_attempts} attempts. "
                f"Mutation allowed with competence penalty.\n",
                file=sys.stderr,
            )

    def _build_app(
        self,
        gr: Any,
        question: str,
        attempt_number: int,
        packet: QAPacket,
        result_q: queue.Queue[str],
    ) -> Any:
        qtype_label = packet.question_type.replace("_", " ").title()
        with gr.Blocks(title="VibeCheck QA", theme=gr.themes.Soft()) as app:
            gr.Markdown(
                f"## VibeCheck QA — Attempt {attempt_number}/{self.max_attempts}\n"
                f"**Type:** {qtype_label}"
            )
            gr.Markdown(question)

            if packet.question_type == "faded_example":
                answer_input = gr.Code(
                    language="python",
                    label="Fill in the code",
                    lines=8,
                )
            else:
                answer_input = gr.Textbox(
                    label="Your answer",
                    lines=4,
                    placeholder="Type your answer here...",
                )

            submit_btn = gr.Button("Submit", variant="primary")
            status_text = gr.Markdown("")

            def on_submit(answer_text: str) -> str:
                if not answer_text or not answer_text.strip():
                    return "Please provide an answer before submitting."
                result_q.put(answer_text.strip())
                return "Answer submitted. You can close this window."

            submit_btn.click(
                fn=on_submit,
                inputs=[answer_input],
                outputs=[status_text],
            )

        return app

    def _launch_app(self, app: Any) -> None:
        with contextlib.suppress(Exception):
            app.launch(
                share=False,
                quiet=True,
                prevent_thread_lock=True,
            )

    def _close_app(self, app: Any) -> None:
        with contextlib.suppress(Exception):
            app.close()

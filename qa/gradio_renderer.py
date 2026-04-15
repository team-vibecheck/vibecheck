"""Gradio-backed QA renderer for VibeCheck.

Provides a browser-based code editor UI for faded_example questions.
Falls back gracefully when gradio is not installed.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any

from core.models import QAPacket

_TIMEOUT_SECONDS = 540  # 9 minutes
_SUBMIT_HANDOFF_SECONDS = 0.75
_SHUTDOWN_GRACE_SECONDS = 1.0


def gradio_available() -> bool:
    return importlib.util.find_spec("gradio") is not None


class GradioQARenderer:
    """Blocking QA renderer that launches a local Gradio app for each question."""

    def __init__(self, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts

    def ask(
        self,
        question: str,
        attempt_number: int,
        packet: QAPacket,
        *,
        session_id: str = "",
        proposal_id: str = "",
        tool_use_id: str = "",
    ) -> str:
        del session_id, proposal_id, tool_use_id
        try:
            gr = _import_gradio()
        except ImportError as exc:
            raise RuntimeError(
                "Gradio is not installed. Install with: uv pip install 'vibecheck[ui]'"
            ) from exc

        result_q: queue.Queue[str] = queue.Queue()
        app = self._build_app(gr, question, attempt_number, packet, result_q)

        local_url: str | None = None
        share_url: str | None = None
        try:
            _, local_url, share_url = self._launch_app(app)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to launch Gradio QA UI: {exc}") from exc

        self._announce_urls(local_url, share_url)
        self._best_effort_open_browser(local_url, share_url)

        try:
            answer = result_q.get(timeout=_TIMEOUT_SECONDS)
        except queue.Empty:
            answer = ""
        finally:
            time.sleep(_SHUTDOWN_GRACE_SECONDS)
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
                cleaned = answer_text.strip()
                threading.Timer(_SUBMIT_HANDOFF_SECONDS, lambda: result_q.put(cleaned)).start()
                return "Answer submitted. Returning to Claude..."

            submit_btn.click(
                fn=on_submit,
                inputs=[answer_input],
                outputs=[status_text],
            )

        return app

    def _launch_app(self, app: Any) -> tuple[Any, str | None, str | None]:
        return app.launch(
            share=False,
            quiet=True,
            inbrowser=False,
            prevent_thread_lock=True,
            server_name="127.0.0.1",
        )

    def _announce_urls(self, local_url: str | None, share_url: str | None) -> None:
        print("\nVibeCheck QA web UI launched.", file=sys.stderr)
        if local_url:
            print(f"Open local URL: {local_url}", file=sys.stderr)
        if share_url:
            print(f"Open share URL: {share_url}", file=sys.stderr)
        print(
            "If the browser did not open automatically, copy/paste a URL above.",
            file=sys.stderr,
        )

    def _best_effort_open_browser(self, local_url: str | None, share_url: str | None) -> None:
        target = share_url or local_url
        if not target:
            return

        with contextlib.suppress(Exception):
            if webbrowser.open(target, new=1):
                return

        if _is_wsl():
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["wslview", target],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return

        with contextlib.suppress(Exception):
            subprocess.run(
                ["xdg-open", target],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _close_app(self, app: Any) -> None:
        with contextlib.suppress(Exception):
            app.close()


def _import_gradio() -> Any:
    return importlib.import_module("gradio")


def _is_wsl() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    for env_name in ("WSL_DISTRO_NAME", "WSL_INTEROP"):
        if os.environ.get(env_name):
            return True
    try:
        with open("/proc/version", encoding="utf-8") as version_file:
            data = version_file.read().lower()
    except OSError:
        return False
    return "microsoft" in data

from __future__ import annotations

import sys
from pathlib import Path

from core.models import QAPacket

_SEPARATOR = "─" * 60
_MAX_ATTEMPTS_DEFAULT = 3


class TerminalQARenderer:
    def __init__(self, max_attempts: int = _MAX_ATTEMPTS_DEFAULT) -> None:
        self.max_attempts = max_attempts

    def ask(self, question: str, attempt_number: int, packet: QAPacket) -> str:
        header = self._format_header(attempt_number, packet)
        body = self._format_body(question, packet)
        prompt_text = f"\n{_SEPARATOR}\n{header}\n{_SEPARATOR}\n\n{body}\n\n> "

        answer = self._read_answer(prompt_text)
        if not answer:
            self._write_output("\n  (empty answer received)\n")
        return answer

    def show_feedback(self, feedback: str, *, passed: bool) -> None:
        if passed:
            self._write_output(f"\n  ✓ Correct.\n")
        else:
            self._write_output(f"\n  ✗ Not quite. {feedback}\n")

    def show_outcome(self, *, passed: bool, attempt_count: int) -> None:
        self._write_output(f"\n{_SEPARATOR}\n")
        if passed:
            self._write_output(
                f"  PASSED on attempt {attempt_count}/{self.max_attempts}. "
                f"Mutation will proceed.\n"
            )
        else:
            self._write_output(
                f"  FAILED after {attempt_count}/{self.max_attempts} attempts. "
                f"Mutation allowed with competence penalty.\n"
            )
        self._write_output(f"{_SEPARATOR}\n")

    def _format_header(self, attempt_number: int, packet: QAPacket) -> str:
        qtype_label = packet.question_type.replace("_", " ").title()
        return (
            f"  VibeCheck QA — Attempt {attempt_number}/{self.max_attempts}\n"
            f"  Type: {qtype_label}"
        )

    def _format_body(self, question: str, packet: QAPacket) -> str:
        sections: list[str] = []

        if packet.question_type == "faded_example":
            sections.append("Fill in the blanks below:")
            sections.append("")
        elif packet.question_type == "true_false":
            sections.append("Answer True or False:")
            sections.append("")

        sections.append(question)
        return "\n".join(sections)

    def _read_answer(self, prompt_text: str) -> str:
        tty_path = Path("/dev/tty")
        try:
            if tty_path.exists():
                with tty_path.open("r+", encoding="utf-8") as tty:
                    tty.write(prompt_text)
                    tty.flush()
                    line = tty.readline()
                    if not line:
                        return ""
                    return line.strip()

            self._write_output(prompt_text)
            return input().strip()
        except EOFError:
            return ""

    def _write_output(self, text: str) -> None:
        print(text, file=sys.stderr, end="")

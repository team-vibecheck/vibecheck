from __future__ import annotations

from pathlib import Path

from cli.cm_init import run_cm_init


def test_run_cm_init_uses_terminal_survey(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    called: dict[str, bool] = {"terminal": False}

    class FakeModel:
        def __init__(self) -> None:
            self.concepts = {"python_basics": type("Entry", (), {"score": 0.5})()}

    def fake_terminal_survey(output_path: Path):
        called["terminal"] = True
        assert output_path == Path("state") / "competence_model.yaml"
        return FakeModel()

    monkeypatch.setattr("cli.cm_init._terminal_survey", fake_terminal_survey)

    run_cm_init()

    assert called["terminal"] is True

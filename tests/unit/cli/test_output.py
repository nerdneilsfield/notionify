from __future__ import annotations

import json

from notionify.cli.output import Reporter


def test_reporter_result_json_wraps_payload(capsys) -> None:  # type: ignore[no-untyped-def]
    reporter = Reporter(verbosity=0, json_mode=True)

    reporter.result({"value": 1})

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "value": 1}


def test_reporter_step_respects_verbosity(capsys) -> None:  # type: ignore[no-untyped-def]
    reporter = Reporter(verbosity=0, json_mode=False)
    reporter.step("hidden")
    assert capsys.readouterr().err == ""

    reporter = Reporter(verbosity=1, json_mode=False)
    reporter.step("visible")
    assert "visible" in capsys.readouterr().err


def test_reporter_fail_json(capsys) -> None:  # type: ignore[no-untyped-def]
    reporter = Reporter(verbosity=0, json_mode=True)

    rc = reporter.fail(ValueError("bad"), exit_code=7)

    assert rc == 7
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert payload["error_type"] == "ValueError"

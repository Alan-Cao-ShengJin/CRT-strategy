from datetime import datetime
import json

from crt.models import UTC, MINUTE, timestamp
from crt.journal import Journal
from crt.paper import run_rest_paper
from test_integration import fixture_bars, configuration


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return timestamp("2026-11-03T14:32:00Z")


class API:
    def __init__(self, **kwargs):
        pass

    def latest(self, ticker):
        return [{"window_start": int(timestamp("2026-11-03T14:31:00Z").timestamp())*1_000_000_000}]

    def validate_contract(self, *args, **kwargs):
        return {}

    def download(self, ticker, start, end):
        return [b for b in fixture_bars() if int(start) <= int(b.start.timestamp())*1_000_000_000 < int(end)]


def test_rest_resume_backfills_and_executes_without_duplicate_orders(monkeypatch, tmp_path):
    monkeypatch.setattr("crt.paper.MassiveClient", API)
    monkeypatch.setattr("crt.paper.datetime", Clock)
    path = tmp_path / "paper.sqlite"
    cfg = configuration()
    j = Journal(path, cfg)
    for b in fixture_bars()[:30]:
        j.append({"kind": "bars", "bars": [b.to_dict()], "learn_only": True})
    # Retain the actual current setup at the restart boundary.
    j.close()
    args = (cfg, ["MNQZ6"], path, tmp_path / "report")
    first = run_rest_paper(*args, duration=.001)
    assert first["processed_new_bars"] == 2 and first["trades"] == 1
    second = run_rest_paper(*args, duration=.001)
    assert second["processed_new_bars"] == 0 and second["trades"] == 1
    assert first["net_pnl"] == second["net_pnl"]
    assert (tmp_path / "report" / "report.html").exists()


def test_rest_probe_reports_delay_without_trading(monkeypatch):
    monkeypatch.setattr("crt.paper.MassiveClient", API)
    monkeypatch.setattr("crt.paper.datetime", Clock)
    result = run_rest_paper(configuration(), ["MNQZ6"], "unused.sqlite", "unused", probe=True)
    assert result["data"]["MNQZ6"]["age_seconds"] == 0

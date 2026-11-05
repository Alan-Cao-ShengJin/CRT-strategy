import json
from dataclasses import asdict
from datetime import timedelta

import pytest

from crt.config import Config
from crt.data import read_bars, write_bars
from crt.engine import Engine
from crt.journal import Journal
from crt.massive import MassiveClient, MassiveError, contract_root, parse_trade
from crt.models import Bar, MINUTE, Tick, timestamp
from crt.calendar import load_sessions

START = timestamp("2026-11-03T14:00:00Z")


def fixture_bars():
    first = [Bar("MNQ", START+i*MINUTE, 100, 120, 90, 110, 10, "MNQZ6") for i in range(15)]
    second = [Bar("MNQ", START+i*MINUTE, 95, 108, 88, 105, 10, "MNQZ6") for i in range(15, 30)]
    third = [Bar("MNQ", START+30*MINUTE, 108, 110, 108, 109, 10, "MNQZ6"),
             Bar("MNQ", START+31*MINUTE, 115, 120, 115, 119, 10, "MNQZ6")]
    return first + second + third


def configuration():
    return Config(symbols=["MNQ"], entry_mode="direct", chop_enabled=False, neutral_policy="first")


def test_full_pipeline_no_c2_fill_or_future_rewrite():
    bars, e = fixture_bars(), Engine(configuration())
    for b in bars[:30]:
        e.on_bar_batch([b])
    before = list(e.events)
    assert not e.positions and not e.trades
    assert len([x for x in e.events if x["kind"] == "setup"]) == 1
    assert e.states["MNQ"].pending.created == START + 30 * MINUTE
    for b in bars[30:]:
        e.on_bar_batch([b])
    assert e.events[:len(before)] == before
    assert len(e.trades) == 1 and e.trades[0].reason == "target"


def test_journal_replay_is_identical_and_idempotent(tmp_path):
    path = tmp_path / "account.sqlite"
    cfg = configuration()
    j = Journal(path, cfg)
    for b in fixture_bars()[:31]:
        event = {"kind": "bars", "bars": [b.to_dict()]}
        assert j.append(event)
        assert not j.append(event)
    assert "MNQ" in j.engine.positions
    summary, events = j.engine.summary(), list(j.engine.events)
    j.close()
    j = Journal(path, cfg)
    assert j.engine.summary() == summary and j.engine.events == events
    j.append({"kind": "bars", "bars": [fixture_bars()[31].to_dict()]})
    assert len(j.engine.trades) == 1
    j.close()


def test_journal_rejects_changed_risk_settings(tmp_path):
    path = tmp_path / "account.sqlite"
    Journal(path, Config()).close()
    with pytest.raises(ValueError, match="configuration differs"):
        Journal(path, Config(risk_per_trade=200))


def test_data_roundtrip_and_bad_input_rejection(tmp_path):
    path = tmp_path / "history.csv"
    write_bars(path, fixture_bars())
    assert read_bars([str(path)]) == fixture_bars()
    with pytest.raises(ValueError, match="Duplicate"):
        read_bars([str(path), str(path)])
    with pytest.raises(ValueError, match="offset"):
        timestamp("2026-11-03T14:30:00")
    with pytest.raises(ValueError, match="off-tick"):
        Bar("MNQ", START, 100.1, 101, 99, 100)


def test_api_nanoseconds_normalization_and_pagination(monkeypatch):
    client = MassiveClient(key="fixture-secret")
    calls = []
    def request(path, params=None):
        calls.append((path, params))
        return {"results": [{"window_start": int(START.timestamp())*1_000_000_000,
                             "open": 100, "high": 101, "low": 99, "close": 100, "volume": 3}]}
    monkeypatch.setattr(client, "request", request)
    bars = client.download("MNQZ6", "2026-11-03", "2026-11-04")
    assert bars[0].start == START and bars[0].contract == "MNQZ6"
    assert calls[0][1]["resolution"] == "1min"


def test_untrusted_pagination_cannot_receive_key():
    with pytest.raises(MassiveError, match="untrusted"):
        MassiveClient(key="fixture-secret").request("https://example.com/steal")


def test_trade_schema_and_contract_validation():
    tick = parse_trade({"ev": "T", "sym": "MGCZ6", "p": 3900.1, "s": 2,
                        "t": 1793716200000, "q": 42})
    assert tick.symbol == "MGC" and tick.sequence == "42"
    for ticker in ("MNQ", "MNQ1!", "MNQZ6-MNQH7", "AAPL"):
        with pytest.raises(ValueError):
            contract_root(ticker)


def test_news_blackout_blocks_entries(tmp_path):
    path = tmp_path / "news.json"
    path.write_text(json.dumps([{"event_time": "2026-11-03T14:30:00Z", "reason": "test release"}]))
    c = configuration()
    c.blackout_file = str(path)
    e = Engine(c)
    for b in fixture_bars():
        e.on_bar_batch([b])
    assert not e.positions and not e.trades


def test_exchange_schedule_early_close(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps([
        {"product_code": "MNQ", "event": "open", "timestamp": "2026-11-03T00:00:00Z"},
        {"product_code": "MNQ", "event": "open", "timestamp": "2026-11-03T00:00:00Z"},
        {"product_code": "MNQ", "event": "close", "timestamp": "2026-11-03T14:40:00Z"}]))
    sessions = load_sessions(str(path))
    assert len(sessions["MNQ"]) == 1
    c = configuration()
    c.schedule_file = str(path)
    e = Engine(c)
    for b in fixture_bars()[:31]:
        e.on_bar_batch([b])
    assert "MNQ" in e.positions
    e.on_bar_batch([Bar("MNQ", START+35*MINUTE, 110, 111, 109, 110, 5, "MNQZ6")])
    assert e.trades[0].reason == "daily_flatten"


def test_warmup_ready_control_replays(tmp_path):
    path = tmp_path / "warm.sqlite"
    j = Journal(path, configuration())
    for b in fixture_bars()[:30]:
        j.append({"kind": "bars", "bars": [b.to_dict()], "learn_only": True})
    assert j.engine.states["MNQ"].pending
    j.append({"kind": "warmup_ready"})
    j.close()
    j = Journal(path, configuration())
    assert j.engine.states["MNQ"].pending is None and not j.engine.states["MNQ"].selected
    j.close()


@pytest.mark.parametrize("options", [{"reward_risk": float("nan")}, {"risk_fraction": 2}, {"slippage_ticks": -1}, {"timeframes": [5]}, {"symbols": ["MNQ", "MNQ"]}])
def test_invalid_configuration_fails_closed(options):
    with pytest.raises(ValueError):
        Config(**options).validate()

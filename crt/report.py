from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict, fields
from pathlib import Path

from .models import Trade


def write_report(engine, output, metadata=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summary = engine.summary()
    document = {"summary": summary, "config": engine.config.to_dict(), "metadata": metadata or {},
                "open_positions": {s: {"entry": p.entry, "stop": p.stop, "target": p.setup.target, "qty": p.qty} for s, p in engine.positions.items()}}
    (output / "summary.json").write_text(json.dumps(document, indent=2, allow_nan=False), encoding="utf-8")
    with (output / "trades.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[f.name for f in fields(Trade)])
        writer.writeheader()
        writer.writerows(asdict(t) for t in engine.trades)
    with (output / "events.jsonl").open("w", encoding="utf-8") as stream:
        for event in engine.events:
            stream.write(json.dumps(event) + "\n")
    with (output / "equity.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["time", "equity"])
        writer.writeheader()
        writer.writerows(engine.curve)
    values = [p["equity"] for p in engine.curve] or [engine.equity]
    low, high = min(values), max(values)
    points = " ".join(f"{i / max(1, len(values)-1) * 1000:.2f},{180 - (v-low)/max(high-low,1)*160:.2f}" for i, v in enumerate(values))
    def display(key, value):
        if value is None:
            return "—"
        if key == "win_rate":
            return f"{value:.2%}"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, float):
            return f"{value:,.2f}"
        return str(value).replace("_", " ") or "None"
    cards = "".join(f'<div class="card"><span>{html.escape(k.replace("_", " "))}</span><strong class="{("text" if isinstance(v, str) else "number")}">{html.escape(display(k, v))}</strong></div>' for k, v in summary.items())
    rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in (t.symbol, t.contract, "LONG" if t.side == 1 else "SHORT", t.entry_time, t.entry, t.exit, t.qty, round(t.net, 2), t.reason)) + "</tr>" for t in engine.trades)
    content = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRT research report</title><style>
body{{font:15px system-ui;background:#101723;color:#e3ebf7;margin:0;padding:clamp(16px,4vw,54px)}}
h1{{font-size:clamp(26px,4vw,44px);margin-bottom:8px}}p{{max-width:90ch;color:#aebbd0;line-height:1.6}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:26px 0}}
.card{{padding:18px;background:#1b2637;border:1px solid #304058;border-radius:9px;min-width:0}}span,strong{{display:block}}span{{color:#aebbd0;font-size:12px;text-transform:uppercase}}strong{{font-size:24px;margin-top:8px;overflow-wrap:anywhere}}strong.text{{font-size:18px}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;white-space:nowrap;width:100%}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #304058}}svg{{width:100%;height:220px;background:#1b2637;border-radius:9px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;color:#aebbd0}}
</style><h1>CRT / Futures</h1><p>MNQ · MES · MGC | Research and simulated execution</p>
<p>Results include configured commissions and slippage. Bar rebreaks require separate minutes. Ambiguous entry bars favor stops and defer targets. These are execution assumptions, not a proven lower bound on results. No broker orders were sent.</p>
<div class="cards">{cards}</div><h2>Marked equity</h2><p>Range: ${low:,.2f} – ${high:,.2f}. X axis follows observed samples; overnight gaps are compressed.</p>
<svg viewBox="0 0 1000 200" role="img" aria-label="Marked account equity over observed samples"><polyline points="{points}" fill="none" stroke="#65d8cb" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
<h2>Trade journal</h2><div class="scroll"><table><thead><tr><th>Symbol</th><th>Contract</th><th>Side</th><th>Entry UTC</th><th>Entry</th><th>Exit</th><th>Qty</th><th>Net USD</th><th>Exit reason</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Run details</h2><pre>{html.escape(json.dumps(document, indent=2))}</pre></html>'''
    (output / "report.html").write_text(content, encoding="utf-8")
    return summary

"""Honest replay mode: recorded decisions JSONL -> simulated fills -> report.

Reads the decisions.jsonl written by `paper`. Each line must carry the
decision and the venue quotes used at that cycle. Produces report.json and
report.html, both labeled as a replay of simulated fills.

This is not live trading and not a profitability claim: it replays what the
paper loop recorded, nothing more.
"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path

from .decision import Decision
from .sim import Portfolio, mark_to_market, simulate
from .store import new_run_dir, write_json
from .venues import Quote

BANNER = ("Replay of recorded paper decisions - simulated fills only. "
          "Not live trading; not a profitability claim.")


def _quote_from(record: dict, venue: str) -> Quote | None:
    q = (record.get("quotes") or {}).get(venue)
    if not q:
        return None
    # Replay treats the recorded quote as the market for that cycle.
    return Quote(
        venue=venue,
        bid=q.get("bid"),
        ask=q.get("ask"),
        mid=q.get("mid"),
        spread_bps=q.get("spread_bps"),
        ref_price_usd=q.get("ref_price_usd"),
        fetched_at=time.time(),
        ok=bool(q.get("ok")),
        error=q.get("error"),
    )


def _venue_of(target_id: str | None) -> str | None:
    if not target_id:
        return None
    parts = target_id.split("_")
    return "_".join(parts[2:]).lower() if len(parts) >= 3 else None


def replay(decisions_path, config) -> Path:
    path = Path(decisions_path)
    if not path.is_file():
        raise FileNotFoundError(f"decisions file not found: {path}")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        raise ValueError(f"decisions file is empty: {path}")

    run_dir = new_run_dir("backtest", config.state_root)
    portfolio = Portfolio(config.paper_start_usdc, 0.0, config.paper_start_usdc)
    fills = []
    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {i + 1}: invalid JSON: {e}")
        d = rec.get("decision")
        if not isinstance(d, dict) or "operation" not in d:
            raise ValueError(f"line {i + 1}: missing decision object")
        decision = Decision(
            operation=d["operation"],
            target_id=d.get("target_id"),
            confidence=float(d.get("confidence", 0.0)),
            rationale=str(d.get("rationale", "")),
            model=str(d.get("model", "")),
            mock=bool(d.get("mock", False)),
        )
        venue = _venue_of(decision.target_id)
        quotes = {}
        if venue:
            q = _quote_from(rec, venue)
            if q is not None:
                quotes[venue] = q
        fill = simulate(decision, quotes, portfolio, config, rec.get("cycle", i + 1))
        fills.append(fill.to_dict())

    ref_mid = None
    for ln in reversed(lines):
        m = (json.loads(ln).get("quotes") or {}).get("jupiter", {}).get("mid")
        if isinstance(m, (int, float)) and m > 0:
            ref_mid = m
            break
    mtm = mark_to_market(portfolio, ref_mid)
    report = {
        "banner": BANNER,
        "source": str(path),
        "cycles": len(lines),
        "fills": fills,
        "filled": sum(1 for f in fills if f["status"] == "filled"),
        "rejected": sum(1 for f in fills if f["status"] == "rejected"),
        "skipped": sum(1 for f in fills if f["status"] == "skipped"),
        "portfolio": portfolio.to_dict(),
        "mark_to_market": mtm,
        "config": config.redacted_summary(),
    }
    write_json(run_dir / "report.json", report)
    (run_dir / "report.html").write_text(_render_html(report), encoding="utf-8")
    return run_dir


def _render_html(report: dict) -> str:
    rows = []
    for f in report["fills"]:
        rows.append(
            "<tr><td>{cycle}</td><td>{venue}</td><td>{side}</td>"
            "<td>{status}</td><td>{size_sol}</td><td>{price}</td>"
            "<td>{notional}</td><td>{fee}</td><td>{reason}</td></tr>".format(
                cycle=f["cycle"], venue=html.escape(str(f["venue"])),
                side=html.escape(str(f["side"])), status=html.escape(f["status"]),
                size_sol=f["size_sol"],
                price=f["price_usdc"], notional=f["notional_usdc"], fee=f["fee_usdc"],
                reason=html.escape(f["reason"]),
            )
        )
    mtm = report["mark_to_market"] or {}
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>clawd-jev backtest replay</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2em auto;padding:0 1em}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:4px 8px;font-size:13px}}
.banner{{background:#fff8e1;border:1px solid #e0c36a;padding:12px;border-radius:6px}}</style>
</head><body>
<h1>clawd-jev backtest - replay report</h1>
<p class="banner"><strong>{html.escape(report["banner"])}</strong><br>
Source: {html.escape(report["source"])} &middot; Cycles: {report["cycles"]} &middot;
Filled: {report["filled"]} &middot; Rejected: {report["rejected"]} &middot; Skipped: {report["skipped"]}</p>
<h2>Paper portfolio</h2>
<p>USDC {report["portfolio"]["usdc"]} &middot; SOL {report["portfolio"]["sol"]}
&middot; Mark-to-market total {mtm.get("total_usdc")} USDC
(PnL {mtm.get("pnl_usdc")} USDC, {mtm.get("pnl_pct")}%)</p>
<h2>Fills</h2>
<table><tr><th>cycle</th><th>venue</th><th>side</th><th>status</th><th>size SOL</th>
<th>price</th><th>notional</th><th>fee</th><th>reason</th></tr>
{''.join(rows)}
</table>
</body></html>
"""

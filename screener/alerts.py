"""Daily e-mail summary (Gmail SMTP with an App Password)."""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .utils import log

BLUE, RED, GREEN = "#0C4DA1", "#ED1B24", "#1E8E3E"
ACTION_COLOR = {"EXIT": RED, "REDUCE": "#E37400", "HOLD": "#5F6368", "ADD": GREEN}


def _pct(v):
    return "–" if v is None else f"{v * 100:+.1f}%"


def build_html(market: dict, pf: dict | None, strategies: list[dict], top_buys: list[dict], tracking: dict,
               dashboard_url: str | None) -> str:
    h = [f"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:720px;color:#202124'>",
         f"<h2 style='color:{BLUE};margin:0 0 4px'>NEPSE Screener — {market['date']}</h2>",
         f"<p style='margin:0 0 14px'>NEPSE <b>{market['index']:,.1f}</b> ({_pct(market['index_chg'])}) · "
         f"Regime: <b style='color:{GREEN if market['regime'] > 0 else RED if market['regime'] < 0 else '#E37400'}'>{market['regime_label']}</b> · "
         f"Breadth: {market['breadth_50'] * 100:.0f}% above 50-DMA</p>"]
    if pf and pf.get("positions"):
        s = pf["summary"]
        h.append(f"<h3 style='color:{BLUE}'>Your portfolio</h3><p>Value Rs {s['value']:,.0f} · P&L Rs {s['pnl']:,.0f} ({_pct(s['pnl_pct'])})<br><i>{s['regime_advice']}</i></p>")
        h.append("<table style='border-collapse:collapse;width:100%;font-size:13px'><tr style='background:#F1F3F4'>"
                 "<th align=left>Stock</th><th>Action</th><th align=right>LTP</th><th align=right>P&L</th><th align=right>Stop</th><th align=left>Why</th></tr>")
        for p in pf["positions"]:
            c = ACTION_COLOR.get(p["action"], "#5F6368")
            why = p["action_detail"] + ("<br><small>" + " ".join(p["notes"]) + "</small>" if p["notes"] else "")
            h.append(f"<tr style='border-top:1px solid #E8EAED'><td><b>{p['symbol']}</b></td>"
                     f"<td style='color:{c};font-weight:700' align=center>{p['action']}{' ' + str(p['sell_qty']) if p['sell_qty'] else ''}</td>"
                     f"<td align=right>{p['ltp']:,.1f}</td><td align=right>{_pct(p['pnl_pct'])}</td>"
                     f"<td align=right>{p['stop'] or '–'}</td><td>{why}</td></tr>")
        h.append("</table>")
    if top_buys:
        h.append(f"<h3 style='color:{BLUE}'>Top model picks</h3><ol>")
        for r in top_buys:
            h.append(f"<li><b>{r['symbol']}</b> ({r['sector']}) — {r['stance']}, score {r['score']:.0f}, LTP {r['close']:,.1f}</li>")
        h.append("</ol>")
    sig = [s for s in strategies if s.get("current", {}).get("entries") or s.get("current", {}).get("picks")]
    if sig:
        h.append(f"<h3 style='color:{BLUE}'>Strategy signals today</h3><ul>")
        for s in sig:
            cur = s["current"]
            names = cur.get("entries") or cur.get("picks")
            h.append(f"<li><b>{s['name']}</b>: {', '.join(names[:10])}</li>")
        h.append("</ul>")
    sim = tracking.get("simulated", {}).get("by_stance", [])
    live = tracking.get("live", {}).get("by_stance", [])
    hz = tracking.get("main_horizon", 20)
    def line(rows, label):
        b = next((r for r in rows if r["label"] in ("BUY", "STRONG BUY") and r.get(f"hit_{hz}") is not None), None)
        return f"{label}: BUY calls beat NEPSE {b[f'hit_{hz}'] * 100:.0f}% of the time over {hz} sessions (n={b[f'n_{hz}']})" if b else ""
    t = " · ".join(x for x in (line(live, "Live"), line(sim, "Simulated")) if x)
    if t:
        h.append(f"<p style='font-size:13px;color:#5F6368'>Track record — {t}</p>")
    if dashboard_url:
        h.append(f"<p><a href='{dashboard_url}' style='color:{BLUE}'>Open the dashboard →</a></p>")
    h.append("<p style='font-size:11px;color:#9AA0A6'>Automated analysis for personal research. Not investment advice.</p></div>")
    return "".join(h)


def send(subject: str, html: str) -> bool:
    user, pwd, to = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD"), os.environ.get("ALERT_TO")
    if not (user and pwd):
        log.info("E-mail skipped (GMAIL_USER / GMAIL_APP_PASSWORD not set)")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to or user
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, pwd)
            s.sendmail(user, [x.strip() for x in (to or user).split(",")], msg.as_string())
        log.info("E-mail sent to %s", to or user)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("E-mail failed: %s", e)
        return False

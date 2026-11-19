/*
 * NEPSE Screener advice panel for the Samvardhan Capital web app (Netlify).
 *
 * Add ONE line before </body> in the app's index.html:
 *
 *   <script src="screener-advice.js" data-bridge="https://script.google.com/macros/s/XXXX/exec"></script>
 *
 * and upload this file next to index.html. It adds a "Screener" button at the
 * bottom right. The first time it is opened it asks for the bridge key (the
 * API_KEY in NepseScreenerBridge.gs) and keeps it in this browser only, so the
 * key is never written into the public page.
 *
 * To skip the key prompt on every device, set VIEW_KEY in the bridge and add it
 * to the tag: data-key="your-view-key". That key can only read the advice.
 *
 * It reads ?action=advice from the bridge: the advice the daily screener run
 * last wrote for this portfolio (action, sell quantity, trailing stop, reasons).
 */
(function () {
  const me = document.currentScript;
  const BRIDGE = (me && me.dataset.bridge) || '';
  const PAGE_KEY = (me && me.dataset.key) || '';
  const KEY_NAME = 'smv_screener_key';
  const RED = '#ED1B24', BLUE = '#0C4DA1';
  const ACTION = {
    EXIT: { bg: '#FDDCDD', fg: '#A3111A', label: 'Exit' },
    REDUCE: { bg: '#FEF3E2', fg: '#8A5300', label: 'Reduce' },
    HOLD: { bg: '#EEF2F8', fg: '#33415C', label: 'Hold' },
    ADD: { bg: '#E3F1E8', fg: '#16653A', label: 'Add' },
    REVIEW: { bg: '#EEF2F8', fg: '#33415C', label: 'Review' },
  };

  const css = `
  .sca-btn{position:fixed;right:18px;bottom:18px;z-index:9998;background:${BLUE};color:#fff;border:0;border-radius:24px;
    padding:11px 18px;font:600 14px system-ui,-apple-system,Segoe UI,Roboto,sans-serif;box-shadow:0 4px 14px rgba(12,77,161,.35);cursor:pointer}
  .sca-btn:focus-visible,.sca-x:focus-visible,.sca-panel button:focus-visible{outline:3px solid ${RED};outline-offset:2px}
  .sca-wrap{position:fixed;inset:0;z-index:9999;background:rgba(10,20,40,.45);display:none}
  .sca-wrap.on{display:block}
  .sca-panel{position:absolute;right:0;top:0;bottom:0;width:min(760px,100%);background:#fff;color:#1A2333;overflow:auto;
    font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  .sca-head{position:sticky;top:0;background:${BLUE};color:#fff;padding:14px 18px;display:flex;align-items:center;gap:12px}
  .sca-head h2{margin:0;font-size:17px;flex:1}
  .sca-x{background:transparent;border:0;color:#fff;font-size:22px;cursor:pointer;line-height:1}
  .sca-body{padding:14px 18px 40px}
  .sca-regime{border-left:4px solid ${RED};background:#FFF4F4;padding:10px 12px;margin:0 0 14px}
  .sca-meta{color:#5B6B85;font-size:12.5px;margin:0 0 12px}
  .sca-counts{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px}
  .sca-counts span{border-radius:14px;padding:3px 10px;font-weight:600;font-size:12.5px}
  .sca-card{border:1px solid #DCE3EE;border-radius:8px;padding:10px 12px;margin:0 0 8px}
  .sca-row{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
  .sca-sym{font-weight:700;font-size:15px;min-width:64px}
  .sca-act{border-radius:4px;padding:1px 8px;font-weight:700;font-size:12px}
  .sca-num{color:#33415C;font-variant-numeric:tabular-nums}
  .sca-detail{margin:6px 0 0;color:#33415C}
  .sca-why{margin:4px 0 0;color:#5B6B85;font-size:12.5px}
  .sca-h3{font-size:14px;color:${BLUE};margin:20px 0 8px}
  .sca-ideas{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  .sca-ideas th,.sca-ideas td{text-align:left;padding:6px 8px;border-bottom:1px solid #E6EBF2}
  .sca-ideas th{color:#5B6B85;font-weight:600;font-size:12.5px}
  .sca-msg{padding:12px;background:#F4F7FB;border-radius:6px}
  .sca-panel input{width:100%;padding:9px;border:1px solid #B9C4D6;border-radius:6px;font:inherit;margin:8px 0}
  .sca-panel .sca-go{background:${BLUE};color:#fff;border:0;border-radius:6px;padding:9px 14px;font-weight:600;cursor:pointer}
  .sca-link{background:none;border:0;color:${BLUE};text-decoration:underline;cursor:pointer;padding:0;font:inherit;font-size:12.5px}
  /* the host app may style tables, paragraphs and headings (e.g. a dark theme): set everything explicitly */
  .sca-panel,.sca-panel p,.sca-panel span,.sca-panel div,.sca-panel table,.sca-panel tr,.sca-panel label{color:#1A2333!important;text-align:left}
  .sca-panel td{color:#1A2333!important;background:#fff!important;font-size:13.5px!important;border-top:0!important;border-left:0!important;border-right:0!important}
  .sca-panel th{color:#5B6B85!important;background:#fff!important;font-size:12.5px!important;text-transform:none!important;letter-spacing:0!important}
  .sca-panel tr{background:#fff!important}
  .sca-panel .sca-meta,.sca-panel .sca-why{color:#5B6B85!important}
  .sca-panel .sca-detail{color:#33415C!important}
  .sca-panel .sca-h3{color:${BLUE}!important;background:none!important}
  .sca-panel .sca-head,.sca-panel .sca-head h2,.sca-panel .sca-x{color:#fff!important;background:${BLUE}!important}
  .sca-panel .sca-act,.sca-panel .sca-counts span{color:inherit}
  .sca-panel .sca-over{color:${RED}!important;font-weight:700!important}
  .sca-panel b{color:inherit!important}
  @media (prefers-reduced-motion:no-preference){.sca-panel{animation:sca-in .18s ease-out}@keyframes sca-in{from{transform:translateX(24px);opacity:.6}}}`;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const n0 = v => v == null || v === '' ? '' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
  const n2 = v => v == null || v === '' ? '' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function init() {
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    const btn = document.createElement('button');
    btn.className = 'sca-btn'; btn.type = 'button'; btn.textContent = 'Screener advice';
    const wrap = document.createElement('div');
    wrap.className = 'sca-wrap';
    wrap.innerHTML = `<div class="sca-panel" role="dialog" aria-modal="true" aria-label="Screener advice">
      <div class="sca-head"><h2>Screener advice</h2><button class="sca-x" type="button" aria-label="Close">×</button></div>
      <div class="sca-body"></div></div>`;
    document.body.append(btn, wrap);
    const body = wrap.querySelector('.sca-body');
    const close = () => { wrap.classList.remove('on'); btn.focus(); };
    btn.onclick = () => { wrap.classList.add('on'); wrap.querySelector('.sca-x').focus(); load(body); };
    wrap.querySelector('.sca-x').onclick = close;
    wrap.onclick = e => { if (e.target === wrap) close(); };
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && wrap.classList.contains('on')) close(); });
  }

  function askKey(body, msg) {
    body.innerHTML = `<p class="sca-msg">${esc(msg || 'Enter the bridge key once. It stays in this browser only.')}</p>
      <label for="sca-key">Bridge key</label><input id="sca-key" type="password" autocomplete="off">
      <button class="sca-go" type="button">Show advice</button>`;
    const inp = body.querySelector('input');
    const go = () => { if (inp.value.trim()) { try { localStorage.setItem(KEY_NAME, inp.value.trim()); } catch (e) {} load(body); } };
    body.querySelector('.sca-go').onclick = go;
    inp.onkeydown = e => { if (e.key === 'Enter') go(); };
    inp.focus();
  }

  async function load(body) {
    if (!BRIDGE) { body.innerHTML = '<p class="sca-msg">Add data-bridge="…/exec" to the script tag.</p>'; return; }
    let key = PAGE_KEY; if (!key) try { key = localStorage.getItem(KEY_NAME) || ''; } catch (e) {}
    if (!key) return askKey(body);
    body.innerHTML = '<p class="sca-meta">Loading…</p>';
    try {
      const r = await fetch(BRIDGE + '?action=advice&key=' + encodeURIComponent(key));
      const js = await r.json();
      if (!js.ok) {
        if (/key/i.test(js.error || '') && PAGE_KEY) { body.innerHTML = '<p class="sca-msg">The key in the page was not accepted. Check VIEW_KEY in the bridge script.</p>'; return; }
        if (/key/i.test(js.error || '')) { try { localStorage.removeItem(KEY_NAME); } catch (e) {} return askKey(body, 'That key was not accepted. Enter it again.'); }
        body.innerHTML = `<p class="sca-msg">${esc(js.error || 'No advice yet.')} The screener sends it after its evening run.</p>`;
        return;
      }
      render(body, js);
    } catch (e) {
      body.innerHTML = `<p class="sca-msg">Could not reach the bridge: ${esc(e.message)}</p>`;
    }
  }

  function render(body, js) {
    const s = js.summary || {};
    const pos = js.positions || [];
    const counts = {};
    pos.forEach(p => counts[p.action] = (counts[p.action] || 0) + 1);
    const order = ['EXIT', 'REDUCE', 'ADD', 'HOLD'];
    let h = `<p class="sca-meta">Screener close of ${esc(s.date)} · received ${esc(new Date(js.received).toLocaleString())}</p>`;
    if (s.regime_advice) h += `<p class="sca-regime">${esc(s.regime_advice)}</p>`;
    h += '<div class="sca-counts">' + order.filter(a => counts[a]).map(a =>
      `<span style="background:${ACTION[a].bg};color:${ACTION[a].fg}!important">${counts[a]} ${ACTION[a].label}</span>`).join('') + '</div>';
    pos.forEach(p => {
      const a = ACTION[p.action] || ACTION.HOLD;
      const vs = p.stop && p.ltp ? ((p.ltp / p.stop - 1) * 100).toFixed(1) + '% vs stop' : '';
      h += `<div class="sca-card"><div class="sca-row">
        <span class="sca-sym">${esc(p.symbol)}</span>
        <span class="sca-act" style="background:${a.bg};color:${a.fg}!important">${a.label}${p.sell_qty && (p.action === 'EXIT' || p.action === 'REDUCE') ? ' ' + n0(p.sell_qty) : ''}</span>
        <span class="sca-num">LTP ${n2(p.ltp)}</span>
        ${p.stop ? `<span class="sca-num">Stop ${n2(p.stop)} (${vs})</span>` : ''}
        <span class="sca-num">Score ${p.score == null ? '' : Math.round(p.score)}</span></div>
        <p class="sca-detail">${esc(p.detail)}</p>
        ${(p.notes || []).length ? `<p class="sca-why">${(p.notes || []).map(esc).join(' ')}</p>` : ''}
        ${(p.reasons || []).length ? `<p class="sca-why">${(p.reasons || []).map(esc).join(' · ')}</p>` : ''}</div>`;
    });
    (s.not_covered || []).forEach(n => {
      h += `<div class="sca-card"><div class="sca-row"><span class="sca-sym">${esc(n.symbol)}</span>
        <span class="sca-act" style="background:${ACTION.REVIEW.bg};color:${ACTION.REVIEW.fg}!important">Review</span></div>
        <p class="sca-detail">${esc(n.note)}</p></div>`;
    });
    const sw = s.sector_weights || {}, lim = (s.sector_limit && s.sector_limit < 1) ? s.sector_limit : Infinity;
    const secs = Object.keys(sw);
    if (secs.length) {
      h += `<h3 class="sca-h3">Sector weights${isFinite(lim) ? ` (limit ${Math.round(lim * 100)}%)` : ''}</h3><table class="sca-ideas"><tbody>` +
        secs.map(k => `<tr><td>${esc(k)}</td><td class="${sw[k] > lim ? 'sca-over' : ''}">${(sw[k] * 100).toFixed(1)}%</td></tr>`).join('') +
        '</tbody></table>';
    }
    const c = js.candidates || [];
    if (c.length) {
      h += `<h3 class="sca-h3">Ideas for new money</h3><table class="sca-ideas"><thead><tr><th>Symbol</th><th>Sector</th><th>Call</th><th>Score</th><th>Close</th></tr></thead><tbody>` +
        c.map(x => `<tr><td><b>${esc(x.symbol)}</b></td><td>${esc(x.sector)}</td><td>${esc(x.stance)}</td><td>${Math.round(x.score)}</td><td>${n2(x.close)}</td></tr>`).join('') +
        '</tbody></table>';
    }
    if (!PAGE_KEY) h += '<p class="sca-meta" style="margin-top:18px"><button class="sca-link" type="button">Change bridge key</button></p>';
    body.innerHTML = h;
    if (!PAGE_KEY) body.querySelector('.sca-link').onclick = () => { try { localStorage.removeItem(KEY_NAME); } catch (e) {} askKey(body); };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

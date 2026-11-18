/**
 * NEPSE Screener ⇄ Google Sheets bridge
 * ------------------------------------------------------------------
 * Put this in a NEW, standalone Apps Script project (script.google.com →
 * New project). It does not touch your existing Apps Script / Netlify app.
 *
 * 1. Sheet → Screener: a key-protected web endpoint that returns your
 *    holdings as JSON. The screener's Portfolio page and the daily e-mail
 *    read it live; nothing is stored in the public GitHub repository.
 * 2. Screener → Sheet: syncScreener() writes the screener's calls, scores
 *    and stop levels into two tabs ("Screener Signals", "Screener Advice")
 *    so your own sheet / Netlify app can use them too.
 *
 * 3. Screener → Sheet (full advice): when the daily screener run reads your
 *    holdings, it posts its position-aware advice (action, sell quantity,
 *    trailing stop, tax, reasons) back here and doPost() writes it into the
 *    "Screener Advice" tab. Your Netlify app can read it with
 *    ?action=advice&key=... (see getAdvice_).
 *
 * Setup: fill in CONFIG, run testSetup() once (authorise), then
 * Deploy → New deployment → Web app (Execute as: Me, Who has access: Anyone),
 * and run installDailyTrigger() once.
 */

const CONFIG = {
  // ID from the sheet URL: https://docs.google.com/spreadsheets/d/<THIS PART>/edit
  SPREADSHEET_ID: 'PASTE_YOUR_SPREADSHEET_ID_HERE',

  // Tab that holds the portfolio. Leave '' to auto-detect the first tab whose
  // header row contains a symbol column and a quantity column.
  HOLDINGS_SHEET: '',

  // Exact header names to use, when auto-detection would pick the wrong column.
  // Example for a holdings tab that has "Buy qty", "Sold qty" and "Held qty":
  //   COLUMNS: { symbol: 'Scrip', quantity: 'Held qty', wacc: 'WACC' },
  COLUMNS: {},

  // Optional: a transactions tab (Date, Type Buy/Sell, Scrip, Qty) used only to
  // find when each current holding was bought, if the holdings tab has no date.
  // The screener measures the trailing stop from the highest close since then.
  BUY_DATES_SHEET: '',

  // Capital gains tax. Leave '' for individuals (5% under a year, 3.75% after).
  // Companies and institutions pay a flat rate: put 0.10 for 10%.
  CGT_FLAT: '',

  // true: the screener sends its full advice and doPost() writes ADVICE_SHEET;
  // syncScreener() then writes only the Signals tab.
  FULL_ADVICE: true,

  // Long random secret. The screener must send the same key. Change it!
  API_KEY: 'CHANGE-ME-to-a-long-random-key',

  PORTFOLIO_NAME: 'Samvardhan Capital Management',

  // Where the screener publishes its data (your GitHub Pages address + /data/)
  SCREENER_DATA_URL: 'https://grgsuman23.github.io/Nepse-Screener/data/',
  SCREENER_APP_URL: 'https://grgsuman23.github.io/Nepse-Screener/',

  // Where syncScreener() writes its tabs. '' = the same spreadsheet.
  // Use a different spreadsheet ID if your existing app must not see new tabs.
  OUTPUT_SPREADSHEET_ID: '',
  SIGNALS_SHEET: 'Screener Signals',
  ADVICE_SHEET: 'Screener Advice',

  // Column names are matched case-insensitively, ignoring spaces and symbols.
  // Add your own header names to these lists if auto-detection misses them.
  // Names starting with '=' must match the whole header exactly.
  HEADERS: {
    symbol:   ['symbol', 'scrip', 'script', 'stocksymbol', 'ticker', 'scripcode', '=stock', '=company'],
    quantity: ['quantity', 'qty', 'currentbalance', 'balance', 'balanceqty', 'units', 'kitta', 'noofshares', '=shares', 'holdingqty', '=holding', '=holdings'],
    wacc:     ['wacc', 'waccrate', 'avgcost', 'averagecost', 'costpershare', 'avgprice', 'averageprice', 'avgrate', 'purchaseprice', 'buyprice', 'costprice', 'purchaserate', 'buyrate', '=rate', '=price', '=cost'],
    total:    ['totalcost', 'investment', 'investedamount', 'costvalue', 'purchaseamount', 'totalamount', 'totalinvestment', '=amount'],
    buy_date: ['buydate', 'purchasedate', 'transactiondate', 'acquisitiondate', 'allotmentdate', 'tradedate', '=date'],
    account:  ['account', 'portfolio', 'client', 'clientname', 'boid', 'demat', 'dematac', 'fund', 'scheme', 'owner'],
    side:     ['type', 'transactiontype', 'buysell', 'side', 'action', 'txntype'],
  },
};

/* ================================================================== API */

function doGet(e) {
  const p = (e && e.parameter) || {};
  try {
    if (p.key !== CONFIG.API_KEY) return json_({ ok: false, error: 'Invalid or missing key' });
    const action = p.action || 'holdings';
    if (action === 'ping') return json_({ ok: true, portfolio: CONFIG.PORTFOLIO_NAME });
    if (action === 'holdings') return json_(holdingsPayload_());
    if (action === 'advice') return json_(getAdvice_());
    return json_({ ok: false, error: 'Unknown action: ' + action });
  } catch (err) {
    return json_({ ok: false, error: String(err && err.message || err) });
  }
}

/** The screener posts its advice here: {key, action: 'advice', advice: {...}}. */
function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (body.key !== CONFIG.API_KEY) return json_({ ok: false, error: 'Invalid or missing key' });
    if (body.action !== 'advice' || !body.advice) return json_({ ok: false, error: 'Nothing to do' });
    if (!CONFIG.FULL_ADVICE) return json_({ ok: false, error: 'FULL_ADVICE is off in CONFIG' });
    const n = writeAdvice_(body.advice);
    return json_({ ok: true, rows: n });
  } catch (err) {
    return json_({ ok: false, error: String(err && err.message || err) });
  }
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function holdingsPayload_() {
  const res = readHoldings_();
  const accounts = Array.from(new Set(res.holdings.map(h => h.account).filter(Boolean))).sort();
  return {
    ok: true,
    portfolio: CONFIG.PORTFOLIO_NAME,
    sheet: res.sheet,
    mode: res.mode,
    updated: new Date().toISOString(),
    accounts: accounts,
    holdings: res.holdings,
    warnings: res.warnings,
    tax: { cgt_flat: CONFIG.CGT_FLAT === '' ? null : Number(CONFIG.CGT_FLAT) },
    accepts_advice: !!CONFIG.FULL_ADVICE,
  };
}

/* ============================================================ READING */

function norm_(s) { return String(s == null ? '' : s).toLowerCase().replace(/[^a-z0-9]/g, ''); }

function num_(v) {
  if (typeof v === 'number') return v;
  const t = String(v == null ? '' : v).replace(/,/g, '').replace(/[^\d.\-]/g, '');
  const n = parseFloat(t);
  return isFinite(n) ? n : NaN;
}

function date_(v) {
  if (v instanceof Date && !isNaN(v)) return Utilities.formatDate(v, 'Asia/Kathmandu', 'yyyy-MM-dd');
  const s = String(v || '').trim();
  const m = s.match(/^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})/);
  if (m) return m[1] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2);
  const d = s ? new Date(s) : null;
  return d && !isNaN(d) ? Utilities.formatDate(d, 'Asia/Kathmandu', 'yyyy-MM-dd') : '';
}

/** Find the header row (within the first 15 rows) and map our fields to column indexes. */
function mapHeaders_(values) {
  const H = CONFIG.HEADERS;
  for (let r = 0; r < Math.min(15, values.length); r++) {
    const row = values[r].map(norm_);
    const find = (names, taken, field) => {
      for (const raw of names) { const n = raw.replace(/^=/, ''); const i = row.indexOf(n); if (i >= 0 && !taken.has(i)) return i; }
      for (const n of names) {
        if (n.charAt(0) === '=') continue;
        const i = row.findIndex((h, k) => h && !taken.has(k) && h.indexOf(n) >= 0 &&
          !(field === 'symbol' && h.indexOf('name') >= 0) && !(field === 'wacc' && /ltp|market|current|close/.test(h)));
        if (i >= 0) return i;
      }
      return -1;
    };
    const taken = new Set();
    const map = {};
    const C = CONFIG.COLUMNS || {};
    for (const f of ['symbol', 'quantity', 'total', 'wacc', 'buy_date', 'account', 'side']) {
      const i = C[f] ? row.indexOf(norm_(C[f])) : find(H[f], taken, f);
      map[f] = i;
      if (i >= 0) taken.add(i);
    }
    if (map.symbol >= 0 && map.quantity >= 0) return { row: r, map: map };
  }
  return null;
}

function readHoldings_() {
  const ss = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  const sheets = CONFIG.HOLDINGS_SHEET ? [ss.getSheetByName(CONFIG.HOLDINGS_SHEET)] : ss.getSheets();
  for (const sh of sheets) {
    if (!sh) throw new Error('Tab not found: ' + CONFIG.HOLDINGS_SHEET);
    const values = sh.getDataRange().getValues();
    const hm = mapHeaders_(values);
    if (!hm) continue;
    const out = parseRows_(values.slice(hm.row + 1), hm.map);
    out.sheet = sh.getName();
    if (CONFIG.BUY_DATES_SHEET && hm.map.buy_date < 0) addBuyDates_(ss, out);
    return out;
  }
  throw new Error('No tab with a symbol column and a quantity column was found. Set HOLDINGS_SHEET or add your header names to CONFIG.HEADERS.');
}

/** Fill buy_date from a transactions tab: the date the current holding run
 *  started (the last time the quantity rose from zero). */
function addBuyDates_(ss, out) {
  const sh = ss.getSheetByName(CONFIG.BUY_DATES_SHEET);
  if (!sh) { out.warnings.push('Buy-dates tab not found: ' + CONFIG.BUY_DATES_SHEET); return; }
  const values = sh.getDataRange().getValues();
  const saved = CONFIG.COLUMNS;
  CONFIG.COLUMNS = {};                       // the ledger has its own headers
  const hm = mapHeaders_(values);
  CONFIG.COLUMNS = saved;
  if (!hm || hm.map.buy_date < 0) { out.warnings.push('No date column on ' + CONFIG.BUY_DATES_SHEET); return; }
  const m = hm.map;
  const rows = values.slice(hm.row + 1)
    .map(r => ({ sym: String(r[m.symbol] || '').trim().toUpperCase(), dt: date_(r[m.buy_date]),
                 q: num_(r[m.quantity]), sell: m.side >= 0 && /sell|sale|sold/i.test(String(r[m.side] || '')) }))
    .filter(x => x.sym && x.dt && isFinite(x.q) && x.q)
    .sort((a, b) => a.dt < b.dt ? -1 : a.dt > b.dt ? 1 : 0);
  const run = {};
  rows.forEach(x => {
    const p = run[x.sym] || (run[x.sym] = { q: 0, start: '' });
    if (x.sell) { p.q = Math.max(0, p.q - Math.abs(x.q)); if (p.q === 0) p.start = ''; }
    else { if (p.q === 0) p.start = x.dt; p.q += Math.abs(x.q); }
  });
  out.holdings.forEach(h => { if (!h.buy_date && run[h.symbol]) h.buy_date = run[h.symbol].start; });
  out.warnings = out.warnings.filter(w => w.indexOf('buy-date') < 0);
}

/** Rows can be a holdings list (one row per stock) or a transaction ledger
 *  (buy and sell rows). Both become one position per account + symbol. */
function parseRows_(rows, m) {
  const warnings = [];
  const ledger = m.side >= 0;
  const pos = {};
  let skipped = 0;
  rows.forEach(r => {
    const sym = String(r[m.symbol] || '').trim().toUpperCase();
    if (!sym || /^(total|grand total|sub ?total)$/i.test(sym)) return;
    let qty = num_(r[m.quantity]);
    if (!isFinite(qty) || qty === 0) { skipped++; return; }
    let rate = m.wacc >= 0 ? num_(r[m.wacc]) : NaN;
    const total = m.total >= 0 ? num_(r[m.total]) : NaN;
    if (!isFinite(rate) && isFinite(total) && qty) rate = Math.abs(total / qty);
    const acct = m.account >= 0 ? String(r[m.account] || '').trim() : '';
    const dt = m.buy_date >= 0 ? date_(r[m.buy_date]) : '';
    const isSell = ledger && /sell|sale|sold/i.test(String(r[m.side] || ''));
    const key = acct + '|' + sym;
    const p = pos[key] || (pos[key] = { symbol: sym, account: acct, quantity: 0, cost: 0, buy_date: '' });
    if (isSell || qty < 0) {
      const q = Math.min(Math.abs(qty), p.quantity);
      const avg = p.quantity ? p.cost / p.quantity : 0;
      p.quantity -= q; p.cost -= avg * q;
      if (p.quantity <= 1e-9) { p.quantity = 0; p.cost = 0; p.buy_date = ''; }
    } else {
      if (p.quantity === 0) p.buy_date = dt;
      else if (dt && (!p.buy_date || dt < p.buy_date)) p.buy_date = dt;
      p.quantity += qty;
      p.cost += isFinite(rate) ? rate * qty : 0;
    }
  });
  const holdings = Object.keys(pos).map(k => pos[k]).filter(p => p.quantity > 0).map(p => ({
    symbol: p.symbol, account: p.account, quantity: Math.round(p.quantity * 1000) / 1000,
    wacc: p.cost > 0 ? Math.round(p.cost / p.quantity * 100) / 100 : null, buy_date: p.buy_date || '',
  }));
  if (m.wacc < 0 && m.total < 0) warnings.push('No cost/WACC column found — profit and tax figures will be estimates.');
  if (m.buy_date < 0) warnings.push('No buy-date column found — capital gains tax will assume short-term.');
  if (skipped) warnings.push(skipped + ' rows skipped (blank or zero quantity).');
  return { holdings: holdings, warnings: warnings, mode: ledger ? 'transactions' : 'holdings' };
}

/* =============================================== SCREENER → SHEET SYNC */

function fetchJSON_(name) {
  const r = UrlFetchApp.fetch(CONFIG.SCREENER_DATA_URL + name + '.json', { muteHttpExceptions: true });
  if (r.getResponseCode() !== 200) throw new Error('Could not load ' + name + '.json (HTTP ' + r.getResponseCode() + ')');
  return JSON.parse(r.getContentText());
}

function syncScreener() {
  const scr = fetchJSON_('screener');
  const mkt = fetchJSON_('market');
  const out = SpreadsheetApp.openById(CONFIG.OUTPUT_SPREADSHEET_ID || CONFIG.SPREADSHEET_ID);
  const call = c => ({ 2: 'STRONG BUY', 1: 'BUY', 0: 'HOLD', '-1': 'REDUCE', '-2': 'EXIT' }[c] || '');
  const by = {};
  scr.rows.forEach(r => by[r.symbol] = r);

  // ---- Screener Signals: every stock
  const head = ['Symbol', 'Sector', 'LTP', 'Change %', 'Call', 'Score', 'RS rating', 'RSI 14', 'Supertrend', 'Support / resistance',
                '50-day EMA', '200-day SMA', 'EPS', 'P/E', 'P/B', 'ROE %', 'Dividend yield %', '1M %', '3M %', 'Liquid', 'Reasons'];
  const rows = scr.rows.slice().sort((a, b) => (b.score || -1) - (a.score || -1)).map(r => [
    r.symbol, r.sector, r.close, pct_(r.chg_pct), call(r.stance_code), rnd_(r.score), rnd_(r.rs_rating), rnd_(r.rsi_14),
    r.st_dir > 0 ? 'Bullish' : r.st_dir < 0 ? 'Bearish' : '', rnd_(r.st_line, 2), rnd_(r.ema_50, 2), rnd_(r.sma_200, 2), rnd_(r.eps, 2), rnd_(r.pe, 1), rnd_(r.pbv, 2),
    pct_(r.roe), pct_(r.div_yield), pct_(r.ret_1m), pct_(r.ret_3m), r.liquid ? 'Yes' : 'No', (r.reasons || []).join('; ')]);
  writeTab_(out, CONFIG.SIGNALS_SHEET, head, rows,
    'NEPSE Screener — data as of ' + scr.date + ' · market regime: ' + mkt.regime_label + ' · NEPSE ' + mkt.index);

  if (CONFIG.FULL_ADVICE) { Logger.log('Synced ' + rows.length + ' stocks (advice comes from the screener run).'); return; }

  // ---- Screener Advice: your holdings joined with the screener's view
  let holdings = [];
  try { holdings = readHoldings_().holdings; } catch (e) { Logger.log('Holdings not read: ' + e); }
  const aHead = ['Account', 'Symbol', 'Quantity', 'WACC', 'LTP', 'Profit %', 'Value', 'Call', 'Score', 'Suggested stop',
                 'Stop basis', 'RSI 14', 'P/E', 'Why', 'Open in screener'];
  const aRows = holdings.map(h => {
    const r = by[h.symbol] || {};
    const ltp = r.close;
    let stop = null, basis = '';
    if (r.st_dir > 0 && r.st_line) { stop = r.st_line; basis = 'Supertrend support'; }
    else if (r.atr_14 && r.spark && r.spark.length) { stop = Math.round((Math.max.apply(null, r.spark.slice(-20).filter(x => x != null)) - 3 * r.atr_14) * 100) / 100; basis = '3× ATR below 20-day high'; }
    const brk = stop && ltp && ltp < stop ? ' (breached)' : '';
    return [h.account, h.symbol, h.quantity, h.wacc, ltp == null ? 'not found' : ltp,
      h.wacc && ltp ? Math.round((ltp / h.wacc - 1) * 1000) / 10 : '', ltp ? Math.round(ltp * h.quantity) : '',
      call(r.stance_code), rnd_(r.score), stop, basis + brk, rnd_(r.rsi_14), rnd_(r.pe, 1), (r.reasons || []).slice(0, 3).join('; '),
      CONFIG.SCREENER_APP_URL + '#portfolio'];
  });
  writeTab_(out, CONFIG.ADVICE_SHEET, aHead, aRows,
    'Full position-aware advice (trailing stop since purchase, tax timing, limits) is on the screener’s Portfolio page.');
  Logger.log('Synced ' + rows.length + ' stocks and ' + aRows.length + ' holdings.');
}

/** Writes the screener's full portfolio advice into ADVICE_SHEET. */
function writeAdvice_(adv) {
  const ss = SpreadsheetApp.openById(CONFIG.OUTPUT_SPREADSHEET_ID || CONFIG.SPREADSHEET_ID);
  const sh = ss.getSheetByName(CONFIG.ADVICE_SHEET) || ss.insertSheet(CONFIG.ADVICE_SHEET, ss.getSheets().length);
  const s = adv.summary || {};
  const head = ['Scrip', 'Sector', 'Held qty', 'WACC', 'LTP', 'Market value', 'Unreal % (gross)', 'Action', 'Sell qty',
                'Trailing stop', 'LTP vs stop', 'Score', 'RSI 14', 'Why', 'Notes'];
  const rows = (adv.positions || []).map(p => [
    p.symbol, p.sector, p.qty, p.wacc, p.ltp, Math.round(p.value || 0), p.pnl_pct,
    p.action, (p.action === 'EXIT' || p.action === 'REDUCE') ? p.sell_qty : 0,
    p.stop || '', p.stop && p.ltp ? p.ltp / p.stop - 1 : '', rnd_(p.score, 1), rnd_(p.rsi_14, 1),
    (p.reasons || []).slice(0, 3).join('; '), [p.action_detail].concat(p.notes || []).join(' ')]);
  (s.not_covered || []).forEach(n => rows.push([n.symbol, '', n.qty, n.wacc || '', '', '', '', 'REVIEW', 0, '', '', '', '', '', n.note]));

  sh.clear();
  const W = head.length;
  sh.getRange(1, 1).setValue('Screener advice: ' + CONFIG.PORTFOLIO_NAME).setFontWeight('bold').setFontSize(14).setFontColor('#0C4DA1');
  sh.getRange(2, 1).setValue('Screener data as of ' + s.date + ' close. Received ' +
    Utilities.formatDate(new Date(), 'Asia/Kathmandu', 'yyyy-MM-dd HH:mm') + ' NPT.' +
    (s.positions != null ? ' ' + s.positions + ' positions screened.' : '')).setFontColor('#5B6B85');
  sh.getRange(3, 1).setValue(s.regime_advice || '').setFontColor(s.regime < 0 ? '#ED1B24' : '#0C4DA1').setFontWeight('bold');
  sh.getRange(5, 1, 1, W).setValues([head]).setFontWeight('bold').setBackground('#0C4DA1').setFontColor('#FFFFFF').setWrap(true);
  const clean = r => r.map(v => (v == null || (typeof v === 'number' && !isFinite(v))) ? '' : v);
  if (rows.length) {
    const body = sh.getRange(6, 1, rows.length, W);
    body.setValues(rows.map(clean));
    sh.getRange(6, 3, rows.length, 1).setNumberFormat('#,##0');
    sh.getRange(6, 4, rows.length, 3).setNumberFormat('#,##0.00');
    sh.getRange(6, 6, rows.length, 1).setNumberFormat('#,##0');
    sh.getRange(6, 7, rows.length, 1).setNumberFormat('0.0%;-0.0%;"-"');
    sh.getRange(6, 9, rows.length, 1).setNumberFormat('#,##0;-#,##0;"-"');
    sh.getRange(6, 10, rows.length, 1).setNumberFormat('#,##0.00');
    sh.getRange(6, 11, rows.length, 1).setNumberFormat('0.0%;-0.0%;"-"');
    sh.getRange(6, 14, rows.length, 2).setWrap(true).setVerticalAlignment('top');
    const colour = { EXIT: '#FDDCDD', REDUCE: '#FEF3E2', HOLD: null, ADD: '#E6F4EA', REVIEW: '#EEF2F8' };
    sh.getRange(6, 8, rows.length, 1).setBackgrounds(rows.map(r => [colour[r[7]] || null])).setFontWeight('bold');
  }
  let r = 6 + rows.length + 1;
  // totals
  sh.getRange(r, 1).setValue('Total').setFontWeight('bold');
  if (rows.length) sh.getRange(r, 6).setFormula('=SUM(F6:F' + (5 + rows.length) + ')').setNumberFormat('#,##0').setFontWeight('bold');
  r += 2;
  // sector concentration
  const sw = s.sector_weights || {};
  const sec = Object.keys(sw).map(k => [k, sw[k]]);
  sh.getRange(r, 1).setValue('Sector concentration (limit ' + Math.round((s.sector_limit || 0.4) * 100) + '% per sector)').setFontWeight('bold').setFontColor('#0C4DA1');
  sh.getRange(r + 1, 1, 1, 2).setValues([['Sector', 'Weight']]).setFontWeight('bold').setBackground('#0C4DA1').setFontColor('#FFFFFF');
  if (sec.length) {
    sh.getRange(r + 2, 1, sec.length, 2).setValues(sec);
    sh.getRange(r + 2, 2, sec.length, 1).setNumberFormat('0.0%');
    sh.getRange(r + 2, 2, sec.length, 1).setFontColors(sec.map(x => [x[1] > (s.sector_limit || 0.4) ? '#ED1B24' : '#000000']));
  }
  r += 3 + sec.length;
  // ideas
  const c = adv.candidates || [];
  sh.getRange(r, 1).setValue('Ideas for new money').setFontWeight('bold').setFontColor('#0C4DA1');
  sh.getRange(r + 1, 1, 1, 6).setValues([['Symbol', 'Sector', 'Call', 'Score', 'Close', 'Size vs normal position']])
    .setFontWeight('bold').setBackground('#0C4DA1').setFontColor('#FFFFFF');
  if (c.length) {
    sh.getRange(r + 2, 1, c.length, 6).setValues(c.map(x => clean([x.symbol, x.sector, x.stance, rnd_(x.score, 1), x.close, x.pos_size])));
    sh.getRange(r + 2, 6, c.length, 1).setNumberFormat('0%');
  }
  sh.setFrozenRows(5);
  sh.setColumnWidth(1, 80); sh.setColumnWidth(2, 130); sh.setColumnWidths(3, 11, 90);
  sh.setColumnWidth(14, 320); sh.setColumnWidth(15, 360);
  saveBig_('LAST_ADVICE', JSON.stringify({
    received: new Date().toISOString(), summary: s,
    positions: (adv.positions || []).map(p => ({ symbol: p.symbol, action: p.action, sell_qty: p.sell_qty, stop: p.stop,
      score: p.score, ltp: p.ltp, detail: p.action_detail, notes: p.notes, reasons: (p.reasons || []).slice(0, 4) })),
    candidates: c.slice(0, 8),
  }));
  return rows.length;
}

/** Script properties hold at most 9 KB each, so long text is stored in parts. */
function saveBig_(name, text) {
  const props = PropertiesService.getScriptProperties();
  const parts = Math.ceil(text.length / 8000) || 1;
  const data = { [name + '_N']: String(parts) };
  for (let i = 0; i < parts; i++) data[name + '_' + i] = text.slice(i * 8000, (i + 1) * 8000);
  props.setProperties(data);
}

function loadBig_(name) {
  const props = PropertiesService.getScriptProperties();
  const n = Number(props.getProperty(name + '_N') || 0);
  let t = '';
  for (let i = 0; i < n; i++) t += props.getProperty(name + '_' + i) || '';
  return t;
}

/** Latest advice as JSON for the Netlify app: ?action=advice&key=... */
function getAdvice_() {
  const t = loadBig_('LAST_ADVICE');
  return t ? Object.assign({ ok: true }, JSON.parse(t)) : { ok: false, error: 'No advice received yet' };
}

function pct_(v) { return v == null ? '' : Math.round(v * 1000) / 10; }
function rnd_(v, d) { if (v == null || v === '' || isNaN(v)) return ''; const k = Math.pow(10, d || 0); return Math.round(v * k) / k; }

function writeTab_(ss, name, head, rows, note) {
  const sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clearContents();
  sh.getRange(1, 1).setValue(note).setFontStyle('italic').setFontColor('#5B6B85');
  sh.getRange(2, 1, 1, head.length).setValues([head]).setFontWeight('bold').setBackground('#0C4DA1').setFontColor('#FFFFFF');
  rows = rows.map(r => r.map(v => (v == null || (typeof v === 'number' && !isFinite(v))) ? '' : v));
  if (rows.length) sh.getRange(3, 1, rows.length, head.length).setValues(rows);
  sh.setFrozenRows(2);
  // colour the call column
  const ci = head.indexOf('Call') + 1;
  if (ci > 0 && rows.length) {
    const colours = rows.map(r => {
      const c = String(r[ci - 1]);
      return [c === 'STRONG BUY' ? '#C8EED8' : c === 'BUY' ? '#E6F4EA' : c === 'REDUCE' ? '#FEF3E2' : c === 'EXIT' ? '#FDDCDD' : null];
    });
    sh.getRange(3, ci, rows.length, 1).setBackgrounds(colours);
  }
}

/* ============================================================ HELPERS */

/** Run once from the editor: authorises access and shows what was read. */
function testSetup() {
  const p = holdingsPayload_();
  Logger.log('Tab: %s  (%s mode)', p.sheet, p.mode);
  Logger.log('Positions: %s   Accounts: %s', p.holdings.length, p.accounts.join(', ') || '(none)');
  Logger.log('First rows: %s', JSON.stringify(p.holdings.slice(0, 5)));
  if (p.warnings.length) Logger.log('Warnings: %s', p.warnings.join(' | '));
}

/** Run once: refresh the screener tabs every day at about 4:30 PM Nepal time. */
function installDailyTrigger() {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'syncScreener').forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('syncScreener').timeBased().everyDays(1).atHour(16).nearMinute(30).inTimezone('Asia/Kathmandu').create();
  Logger.log('Daily sync installed (about 4:30 PM Nepal time).');
}

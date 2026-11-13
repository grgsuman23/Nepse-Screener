# NEPSE Screener

A free screener for the Nepal Stock Exchange that you run yourself. It covers:

| Module | What it does |
|---|---|
| **Screener** | All listed equities with 40+ technical indicators and fundamentals (EPS, P/E, P/B, ROE, dividend yield, Graham number), each compared with **sector peers**. Includes 25 ready-made screens plus custom filters. |
| **Sector analysis** | Sector indices, 1W–12M returns, relative-strength rank, a Relative Rotation Graph (Leading / Weakening / Lagging / Improving), and sector valuation medians. |
| **Composite model** | Eight factor scores (trend, momentum, volume, timing, sector, value, quality, liquidity) are combined into a 0–100 score and a call: **STRONG BUY / BUY / HOLD / REDUCE / EXIT**. The call is gated by the NEPSE market regime. |
| **Strategies & backtests** | 8 built-in strategies, and you can write your own in plain rules. The backtester models NEPSE costs, capital-gains tax, T+2 settlement, circuit limits, liquidity and the 10-kitta minimum lot. |
| **Optimiser** | Grid search with a **train/test split**. Each strategy is labelled *robust*, *fragile*, *over-fitted* or *no edge*. |
| **My Portfolio** | Gives **BUY MORE / HOLD / REDUCE / EXIT** advice for each of your holdings. It uses trailing stops, a loss limit, position and sector limits, and capital-gains-tax timing. Your holdings stay in your browser. |
| **Track Record** | A permanent database of every call. Each call is scored against the real price and the NEPSE index after 5, 20 and 60 sessions, giving hit rates, average excess return and the information coefficient. |
| **Daily e-mail** | Sends the market regime, your portfolio actions, top picks and strategy signals at about 3:50 PM Nepal time. |

Everything runs on **GitHub Actions** (free). The dashboard is a web page on **GitHub Pages** (free). You don't need to install anything on your laptop.

> ⚠️ This is for personal research, not investment advice. The data comes from unofficial sources, so check prices before you trade.

---

## 1. One-time setup (≈ 20 minutes)

1. **Create a GitHub account** at github.com, if you don't have one.
2. **Create a new repository**: click **+ → New repository**, name it `nepse-screener` and choose **Public**. GitHub Pages is free only for public repositories, and your portfolio is never stored in the repo. Click **Create repository**.
3. **Upload the files.** On the new repo page, click **uploading an existing file**. Drag in *all* the files and folders from this package and click **Commit changes**.
   * The folder **`.github`** is hidden on Windows and Mac. If it doesn't upload, create the three workflow files by hand. Use **Add file → Create new file**, type the name `.github/workflows/daily.yml`, paste in the contents, and repeat for `weekly.yml` and `manual.yml`.
4. **Allow the robot to save data**: go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**, and click **Save**.
5. **First data load**: go to the **Actions** tab (click *Enable workflows* if asked). Choose **Manual run**, then **Run workflow**, pick the command **setup** and click **Run**.
   This downloads about 7 years of price history and the fundamentals for every stock. It takes roughly 15–40 minutes.
6. **Turn on the dashboard**: go to **Settings → Pages**. Under *Build and deployment*, choose **Deploy from a branch**, then branch **main** and folder **/docs**, and click **Save**.
   After a minute your dashboard is live at `https://<your-username>.github.io/nepse-screener/`. Bookmark it on your phone too.

**Use it like an app.** Open the dashboard link in Chrome or Edge and click **Install** in the sidebar (or the install icon in the address bar). On a phone, use *Add to Home screen*. It then opens in its own window, works offline with the last downloaded data, and has a light and dark theme. Press **Ctrl K** (or **/**) to search any stock, screen or page. Use keys **1–8** to switch pages, and the ★ to build a watchlist.

From then on, **Daily update** runs automatically at 3:50 PM Nepal time, Sunday–Thursday. **Weekly optimisation** runs every Friday.

### Optional: daily e-mail and portfolio in the e-mail
Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Value |
|---|---|
| `GMAIL_USER` | your Gmail address |
| `GMAIL_APP_PASSWORD` | a Google **App Password** (Google Account → Security → 2-Step Verification → App passwords). This is not your normal password. |
| `ALERT_TO` | where to send the e-mail; comma-separate several addresses |
| `DASHBOARD_URL` | your GitHub Pages link |
| `PORTFOLIO_CSV` | paste your holdings as CSV text (see section 5). Secrets are encrypted and never shown publicly. |

---

## 2. Where the data comes from

| Data | Source (tried in order) | Notes |
|---|---|---|
| Price history | Merolagani chart feed (**bonus/right adjusted**) → NEPSE official API | NEPSE prices are unadjusted. When the official API is used, any one-day drop beyond the ±10% circuit is treated as a bonus or right adjustment and back-adjusted automatically. |
| Daily prices | NEPSE official API (`nepse` library) → Merolagani latest market → Merolagani history | NEPSE's *adjusted previous close* is used to catch book-closure adjustments exactly. |
| Fundamentals | Merolagani company page → ShareSansar company page | Refreshed weekly. Each snapshot is kept so that fundamental backtests become *point-in-time* after a year. |
| NEPSE index | Merolagani → NEPSE API → computed equal-weight index | |

Every run records which sources worked. You can see this under **Help → Data sources** in the dashboard. If something breaks, run **Manual run → diagnose** and look at the log.

**Optional extra fundamentals.** You can add NPL, CAR and similar figures from NRB or quarterly reports in `config/fundamentals_manual.csv`. Bank quality scores will then penalise high NPL.

---

## 3. The model: how a call is made

1. **Factor scores (0–100)**
   * Trend: price vs 50-EMA / 200-SMA, 200-SMA slope, Supertrend, ADX/DI.
   * Momentum: an IBD-style RS rating against every stock, plus the 1-month return.
   * Volume: OBV trend, volume surges on up days, MFI.
   * Timing: RSI in the healthy zone.
   * Sector: the sector's RS rank.
   * Value: P/E, P/B, dividend yield and Graham number, each **percentile-ranked within the sector**.
   * Quality: ROE within the sector, minus an NPL penalty when known.
   * Liquidity: average turnover.
2. **Composite score.** A weighted blend of the factors (weights are in `config/settings.yaml`). Missing factors are re-weighted, not treated as zero.
3. **Call**
   * **STRONG BUY**: score ≥ 75, price above the 200-DMA, 50-EMA above the 200-SMA, liquid, and not overbought.
   * **BUY**: score ≥ 62, above the 200-DMA and liquid.
   * **REDUCE**: score < 40, *or* below the 50-EMA with a bearish Supertrend, *or* overextended (RSI > 80 and more than 25% above the 50-EMA).
   * **EXIT**: score < 28, or a broken trend (below the 200-DMA with a bearish Supertrend).
   * **Bear-market gate.** When the NEPSE index is below its 200-DMA with a falling 50-EMA and weak breadth, no BUY calls are issued. NEPSE stocks fall together, so stock-picking rarely beats this.

Why these choices suit NEPSE: the market is retail-driven and moves in long waves, so momentum persists. Market-wide moves dominate individual stocks. Thinly traded scrips are easy to push around. Valuations differ a lot between sectors (bank vs hydro vs microfinance).

---

## 4. Strategies, backtests and creating your own

Built-in strategies, in `config/strategies.yaml`:

| Strategy | Idea |
|---|---|
| Trend Rider | Buys pullbacks to the 20-EMA inside strong uptrends (EMA stack, ADX), only when the market regime is not bearish. Uses an ATR trailing stop. |
| Momentum Rotation | Each month, holds the best 3–6-month performers above a rising 200-DMA. Goes to cash in a bear market. |
| Volume Breakout | 55-day high on twice the normal volume (Turtle/Darvas style). Exits on a close below the 20-day low. |
| Pullback in Uptrend | RSI-2 below 10 dips in long-term uptrends, held for days only. |
| Supertrend + MACD | A MACD cross confirmed by a bullish Supertrend. |
| Quality at a Reasonable Price | Each quarter, holds the 10 best sector-relative value + ROE stocks that are still in an uptrend. |
| Sector Rotation | The strongest stocks inside the top-ranked sectors. |
| Composite Model | Follows the screener's own BUY calls. This is the backtest of the model itself. |

**What the backtester models:**
* Signals use the close, and trades happen at the next day's open.
* Tiered broker commission and the 0.015% SEBON fee on both sides; a Rs 25 DP charge on sells.
* CGT of 5% (≤ 365 days) or 3.75% (> 365 days).
* T+2 settlement.
* No buying at a locked upper circuit and no selling at a locked lower circuit.
* Order size capped at 10% of the day's volume, minimum 10 kitta, no short selling.

**To create a strategy**, open `config/strategies.yaml` on GitHub, click the ✏️ pencil, copy a block, change the `id` and the rules, and commit. The next run backtests it and shows it on the dashboard. Example:

```yaml
  - id: my_hydro_momentum
    name: My Hydro Momentum
    type: rotation
    rebalance: monthly
    filter: "liquid == 1 and close > sma_200 and sector_rs_rank > 60 and eps > 0"
    rank_by: "ret_3m"
    top_n: 5
    regime_filter: "regime >= 0"
```

Signal-type example: `entry: "cross_above(ema_20, ema_50) and rsi_14 < 70 and vol_ratio > 1.5"` with `exit: "cross_below(ema_20, ema_50)"` and `stop_atr: 3`.

**Expression functions:** `cross_above(a,b)`, `cross_below(a,b)`, `shift(a,n)`, `highest(a,n)`, `lowest(a,n)`, `sma(a,n)`, `ema(a,n)`, `rising(a,n)`, `falling(a,n)`, `change(a,n)`, `bars_since(cond)`, `abs`, `min`, `max`, plus `and`, `or`, `not` and arithmetic.

**Optional settings:** `stop_atr`, `stop_loss_pct`, `take_profit_pct`, `max_hold_days`, `max_positions`, `position_size: equal | atr_risk`, `hold_while` (rotation), `params` + `defaults` (for the optimiser).

**Your own screens:** add them to `config/screens.yaml`. They appear under *My screens* in the dashboard.

### Field reference (use these names in rules)
* Price & trend: `close open high low volume turnover chg_pct sma_20 sma_50 sma_200 ema_9 ema_20 ema_50 ema_200 dist_sma200 dist_ema50 sma200_slope ema50_slope st_line st_dir adx_14 plus_di minus_di`
* Oscillators: `rsi_14 rsi_2 macd macd_signal macd_hist stoch_k stoch_d mfi_14 bb_up bb_mid bb_low bb_pctb bb_width`
* Volatility & volume: `atr_14 atr_pct volatility_20 vol_sma_20 vol_ratio turnover_sma_20 obv obv_ema_20 liquid`
* Levels: `hh_20 hh_55 hh_250 ll_20 ll_55 ll_250` (previous n days), `pct_from_52w_high pct_from_52w_low`
* Returns & strength: `ret_1w ret_1m ret_3m ret_6m ret_12m rs_rating rs_vs_mkt_3m sector_rs_rank`
* Market: `regime` (1 bull / 0 neutral / −1 bear), `breadth_50 breadth_200 mkt_ret_1m mkt_ret_3m mkt_ret_6m`
* Fundamentals: `eps bvps pe pbv roe div_yield earnings_yield graham graham_discount mcap npl car pe_vs_sector pbv_vs_sector sector_pe_median pe_pct_sector roe_pct_sector`
* Model: `score score_trend score_momentum score_volume score_timing score_sector score_value score_quality score_liquidity stance_code` (2 strong buy … −2 exit)

Percentages are fractions: 5% is written `0.05`.

---

## 5. Linking your portfolio

**In the dashboard (private):** open **My Portfolio**, then *Import CSV* or add rows by hand. The data is saved only in that browser.

**For the daily e-mail:** paste the same CSV into the `PORTFOLIO_CSV` secret.

CSV format (MeroShare *My WACC* exports also work; the columns are detected automatically):
```
symbol,quantity,wacc,buy_date
NABIL,100,520.50,2025-06-15
```
The WACC is assumed to already include buy commission (MeroShare's WACC does). The buy date gives the exact CGT rate and the start point for the trailing stop.

**How actions are decided:**
* **EXIT**: close below the trailing stop (3 × ATR under the highest close since you bought, or the Supertrend support), *or* the model says EXIT, *or* the loss is more than 15% below WACC without trend support.
* **REDUCE**: the model says REDUCE (trend weakening, or overextended with profit to book), *or* the position is more than 20% of the portfolio.
* **BUY MORE**: the model says BUY or STRONG BUY, the market regime is not bearish, and there is room within the position and sector limits.
* **HOLD**: everything else.
* **Tax note**: if you're within 30 days of the 365-day mark, it shows how much waiting would save.

All limits are in `config/settings.yaml → portfolio`.

---

## 6. Track record: recommendation vs actual

Each daily run adds to these files. Together they are the database, and they're plain CSV, so they open in Excel.

| File | Contents |
|---|---|
| `data/tracking/recommendations.csv` | date, symbol, sector, call, score, price, market regime: every stock, every day |
| `data/tracking/strategy_signals.csv` | each strategy's ENTRY / EXIT / PICK signals with price |
| `data/tracking/outcomes.csv` | every call with its actual 5/20/60-session return, the NEPSE return, and the excess |
| `data/tracking/portfolio_actions.csv` | your portfolio's actions. Only written if `tracking.log_portfolio_actions: true`; turn this on **only if the repo is private**. |

**Hit definitions:** a BUY-type call is a hit if the stock **beat the NEPSE index** over the horizon. An EXIT/REDUCE call is a hit if the stock **lagged** it. The dashboard also shows the absolute hit rate, average and median return, average excess return, results by sector and by regime, monthly hit rates, and the **information coefficient**: the rank correlation between score and later return. An IC consistently above about 0.03 means the score has real predictive value.

Live calls need 5, 20 or 60 sessions before they can be scored. Until then, the **simulated history** rebuilds what the model would have said over the past ~3 years, so you have statistics from day one. It's always labelled separately from live results.

---

## 7. Costs & tax settings

These are in `config/settings.yaml → costs`. Defaults:
* Broker commission: 0.36% up to Rs 50k, 0.33% up to Rs 5L, 0.31% up to Rs 20L, 0.27% up to Rs 1Cr, 0.24% above.
* SEBON fee 0.015%; DP charge Rs 25.
* CGT for resident individuals: 5% (≤ 365 days) / 3.75% (> 365 days), as gazetted on 22 Sept 2026.

Update them if SEBON or the budget changes the rates.

---

## 8. Running locally (optional)
```bash
pip install -r requirements.txt
python run.py demo          # synthetic demo -> demo/docs (no internet needed)
python run.py diagnose      # test data sources
python run.py backfill      # first download
python run.py daily         # update + analyse
python run.py portfolio --file my_portfolio.csv
python -m pytest -q tests
```
To view the dashboard locally: `cd docs && python -m http.server`, then open http://localhost:8000.

## 9. Troubleshooting
* **No data on the dashboard**: check that the *setup* run finished (Actions tab → green tick) and that Pages points to `/docs`.
* **A source shows FAIL**: run **Manual run → diagnose**. The screener falls back to other sources automatically. If *all* sources fail from GitHub's servers (Nepali sites sometimes block foreign IPs), you can run the same commands from any computer in Nepal and push the `data` folder.
* **Fundamentals warning about look-ahead**: this is expected for the first year. Backtests of fundamental strategies then use today's EPS/BVPS for past dates, which is optimistic. Weekly snapshots build up a true point-in-time history.
* **A ticker is missing**: new listings are added every Friday when the universe refreshes, or run **Manual run → backfill**.

## Honest limitations
* The data sources are unofficial and can change without notice. The pipeline has fallbacks and a diagnose command, but expect occasional repairs.
* Backtests are not guarantees. Prefer strategies marked *robust* out-of-sample, and treat high backtest returns with suspicion.
* The model is rules-based by design. On a market of about 280 stocks with limited history, transparent factor rules are more robust than black-box machine learning. The Track Record tab lets you judge it on real results.

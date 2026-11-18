# Connect a Google Sheet portfolio (e.g. Samvardhan Capital Management)

This connects the screener to a portfolio kept in Google Sheets, in both directions:

* **Sheet → screener.** The screener's **Portfolio** page and the daily e-mail read your holdings live from the sheet, then give buy more / hold / reduce / exit advice. The holdings are **never stored in the public GitHub repository**.
* **Screener → sheet.** Each afternoon the screener's calls, scores and stop levels are written into two tabs, **Screener Signals** and **Screener Advice**. Your existing Apps Script or Netlify app can then use them.

It runs as a **separate** Apps Script project, so your existing script and Netlify app are not touched.

---

## What the sheet needs
A tab with a header row that includes at least:

| Needed | Header names recognised (any one) |
|---|---|
| Symbol | Symbol, Scrip, Script, Ticker, Stock Symbol |
| Quantity | Quantity, Qty, Current Balance, Balance, Units, Kitta, Shares |
| *Cost (recommended)* | WACC, WACC Rate, Avg Cost, Average Price, Cost per Share, Purchase Price, Rate; or a total such as Total Cost / Investment |
| *Buy date (recommended)* | Buy Date, Purchase Date, Transaction Date, Date |
| *Account (optional)* | Account, Portfolio, Client, BOID, Demat, Fund, Scheme |
| *Buy/Sell (optional)* | Type, Transaction Type, Buy/Sell, Side |

* The header row can be anywhere in the first 15 rows, so title rows above it are fine.
* "Total" rows and zero quantities are skipped.
* **Holdings list or transaction ledger, both work.** If there is a Buy/Sell column, buys and sells are netted using the average-cost method, which gives the current position and WACC.
* Several accounts (clients, funds, BOIDs) can share one tab. The screener lets you view them combined or one at a time.
* If your headers use other words, add them to `CONFIG.HEADERS` in the script.

---

## Setup (about 10 minutes)

### 1. Create the script project
1. Go to **script.google.com** and click **New project**. Use the Google account that can open the portfolio sheet.
2. Click **Untitled project** at the top and rename it **NEPSE Screener Bridge**.
3. Delete everything in `Code.gs`. Paste in the whole of **`NepseScreenerBridge.gs`** from this folder, then press **Ctrl + S**.

### 2. Fill in CONFIG at the top of the script
| Setting | What to put |
|---|---|
| `SPREADSHEET_ID` | From the sheet's address: `https://docs.google.com/spreadsheets/d/`**`THIS-LONG-PART`**`/edit` |
| `HOLDINGS_SHEET` | The tab name, e.g. `'Portfolio'`. Leave `''` to let it find the tab automatically. |
| `API_KEY` | Make up a long random password, e.g. `smv-7Qp2-xK9d-4LmW-88aZ`. Keep it private. |
| `PORTFOLIO_NAME` | `'Samvardhan Capital Management'` |
| `SCREENER_DATA_URL` / `SCREENER_APP_URL` | Already set to `https://grgsuman23.github.io/Nepse-Screener/…`. Change only if your address differs. |
| `OUTPUT_SPREADSHEET_ID` | Leave `''` to add the two screener tabs to the same sheet. Put another sheet's ID here if your Netlify app must not see extra tabs. |

Save with **Ctrl + S**.

### 3. Test it and give permission
1. In the function dropdown at the top (next to **Debug**), choose **testSetup** and click **Run**.
2. Google asks for permission: **Review permissions**, choose your account, then **Advanced → Go to NEPSE Screener Bridge (unsafe) → Allow**. "Unsafe" only means you wrote the script yourself and Google hasn't reviewed it.
3. The **Execution log** shows the tab it found, the number of positions and the first five rows. Check that symbols, quantities and WACC look right.

### 4. Publish it as a web app
1. Click **Deploy → New deployment**.
2. Click the gear ⚙ next to "Select type" and choose **Web app**.
3. Set **Execute as:** *Me* and **Who has access:** *Anyone*.
4. Click **Deploy**, then copy the **Web app URL** (it ends in `/exec`).

> **Is "Anyone" safe?** The address returns nothing without your `API_KEY`. The sheet itself stays private, and only this script, running as you, can read it. Keep the URL and key private, and change the key if they ever leak.
>
> If you later edit the script, publish the change with **Deploy → Manage deployments → ✏️ → Version: New version → Deploy**. The URL stays the same.

### 5. Connect the screener app
1. Open your screener and go to **Portfolio**, then **Connect Google Sheet**.
2. Enter a name (e.g. *Samvardhan Capital*), the **Web app URL** and the **key**, then click **Test and save**.
3. The new portfolio appears as a tab next to **My holdings**. Use **All accounts combined** or pick one account. **Refresh from sheet** re-reads it at any time.

The connection is saved in that browser only. Repeat this on each device: phone, office PC, laptop.

### 6. Add it to the daily e-mail (optional)
1. In GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `SHEET_PORTFOLIOS`. Value, one line per portfolio:
   ```
   Samvardhan Capital Management|https://script.google.com/macros/s/XXXX/exec|your-api-key
   ```
3. Save. The next daily e-mail includes a section with advice for each holding.

### 7. Write the screener's calls into the sheet (optional)
1. In the script editor, choose **syncScreener** and click **Run**. Two tabs appear: **Screener Signals** (every stock) and **Screener Advice** (your holdings with call, score and suggested stop).
2. Choose **installDailyTrigger** and click **Run**. The tabs then refresh every day at about 4:30 PM Nepal time, after the screener's daily update.

---

## Using screener data in your Netlify app
The screener publishes public market data (no portfolio data) that any web app can read directly:

```js
const BASE = 'https://grgsuman23.github.io/Nepse-Screener/data/';
const scr = await (await fetch(BASE + 'screener.json')).json();   // every stock: call, score, indicators, fundamentals
const calls = Object.fromEntries(scr.rows.map(r => [r.symbol, r]));
// e.g. calls['NABIL'].stance_code  →  2 strong buy, 1 buy, 0 hold, -1 reduce, -2 exit
//      calls['NABIL'].score, .st_line (Supertrend level), .rsi_14, .pe, .reasons
const market = await (await fetch(BASE + 'market.json')).json(); // regime, breadth, index
```
Or, inside your existing Apps Script, read the **Screener Signals** tab like any other range.

## Troubleshooting
| Message | Fix |
|---|---|
| *Invalid or missing key* | The key in the app or secret doesn't exactly match `API_KEY` in the script. Check for spaces. |
| *No tab with a symbol column and a quantity column* | Set `HOLDINGS_SHEET` to the tab name, or add your header words to `CONFIG.HEADERS`. Then publish a new version (step 4 note). |
| App says *Could not connect* but testSetup works | The deployment must be **Who has access: Anyone**, and the URL must end in `/exec` (not `/dev`). |
| Changes to the script don't show | Publish a **New version** in Manage deployments. |
| A symbol shows *not found* | It isn't an active listed equity in the screener (e.g. a debenture, mutual fund, promoter share or a typo). |

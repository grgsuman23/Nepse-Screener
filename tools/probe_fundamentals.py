"""Probe candidate sources for quarterly fundamentals history (run on GitHub Actions)."""
import json, re, sys, traceback
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
SYM = sys.argv[1] if len(sys.argv) > 1 else "NABIL"

def show(title, s, n=1500):
    print(f"\n===== {title} =====\n{s[:n]}", flush=True)

def hints(html, pat=r"[\w/\.\-\?=&%]*(?:financ|report|quarter|Financ|Report|Quarter|ajax|handler|eps|EPS)[\w/\.\-\?=&%]*"):
    return sorted(set(m for m in re.findall(pat, html) if 6 < len(m) < 160))[:150]

# ---- 1. nepalstock.com frontend bundle: list every API path
try:
    r = requests.get("https://www.nepalstock.com/", headers=UA, timeout=40, verify=False)
    scripts = re.findall(r'src="([^"]+\.js)"', r.text)
    show("nepalstock scripts", "\n".join(scripts))
    paths = set()
    for s in scripts:
        u = s if s.startswith("http") else "https://www.nepalstock.com/" + s.lstrip("/")
        js = requests.get(u, headers=UA, timeout=60, verify=False).text
        paths |= set(re.findall(r'["\'`](/?api/[A-Za-z0-9_\-/\.\$\{\}]+)', js))
        paths |= set(re.findall(r'["\'`]((?:nots|authenticate|web)/[A-Za-z0-9_\-/\.\$\{\}]+)', js))
    show("nepalstock API paths", "\n".join(sorted(paths)), 20000)
except Exception:
    show("nepalstock bundle FAILED", traceback.format_exc())

# ---- 2. NEPSE API: security detail and candidate report endpoints
try:
    from nepse import Nepse
    n = Nepse(); n.setTLSVerification(False)
    sec = n.getSecurityIDKeyMap(); com = n.getCompanyIDKeyMap()
    sid, cid = sec.get(SYM), com.get(SYM)
    show("ids", f"security {sid} company {cid}")
    det = n.requestPOSTAPI(url=f"/api/nots/security/{sid}", payload_generator=n.getPOSTPayloadIDForScrips)
    show("security detail keys", json.dumps(det, default=str)[:3000], 3000)
    for path in [f"/api/nots/application/reports/{cid}", f"/api/nots/application/reports/{sid}",
                 f"/api/nots/application/financial-reports/{cid}", f"/api/nots/company/financial-report/{cid}",
                 f"/api/nots/application/company/{cid}/reports", f"/api/nots/security/financialReport/{sid}",
                 f"/api/nots/application/fiscal-report/{cid}", f"/api/nots/application/quarterly-report/{cid}"]:
        try:
            res = n.requestGETAPI(url=path)
            show("GET " + path, json.dumps(res, default=str)[:2500], 2500)
        except Exception as e:
            print("GET", path, "->", type(e).__name__, str(e)[:200])
except Exception:
    show("nepse api FAILED", traceback.format_exc())

# ---- 3. Merolagani company page
try:
    r = requests.get(f"https://merolagani.com/CompanyDetail.aspx?symbol={SYM}", headers=UA, timeout=40)
    show("merolagani hints", "\n".join(hints(r.text)), 8000)
    for m in re.finditer(r"(?i)(quarter|financial)", r.text):
        pass
    tabs = re.findall(r'id="(ctl00_ContentPlaceHolder1_[^"]+)"', r.text)
    show("merolagani element ids", "\n".join(sorted(set(tabs)))[:6000], 6000)
except Exception:
    show("merolagani FAILED", traceback.format_exc())

# ---- 4. ShareSansar company page
try:
    r = requests.get(f"https://www.sharesansar.com/company/{SYM.lower()}", headers=UA, timeout=40)
    show("sharesansar status", f"{r.status_code} {len(r.text)}")
    show("sharesansar hints", "\n".join(hints(r.text)), 8000)
    i = r.text.find("Financial")
    show("sharesansar around Financial", r.text[max(0, i-1500):i+3000], 4500)
except Exception:
    show("sharesansar FAILED", traceback.format_exc())

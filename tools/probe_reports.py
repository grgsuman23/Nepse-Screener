"""How deep is NEPSE's fiscal-report history? (run on GitHub Actions)"""
import json, sys, time
from nepse import Nepse
n = Nepse(); n.setTLSVerification(False)
com = n.getCompanyIDKeyMap()
syms = sys.argv[1:] or ["NABIL", "NICA", "UPPER", "NTC", "SHL", "CHCL", "NLIC", "SKBBL", "HDL", "API", "SGHL", "GBIME"]
for s in syms:
    t = time.time()
    try:
        res = n.requestGETAPI(url=f"/api/nots/application/reports/{com[s]}")
    except Exception as e:
        print(s, "ERR", e); continue
    rows = []
    for r in res:
        fr = r.get("fiscalReport") or {}
        docs = r.get("applicationDocumentDetailsList") or []
        rows.append((fr.get("financialYear", {}).get("fyNameNepali"), (fr.get("quarterMaster") or {}).get("quarterName", "ANNUAL"),
                     (fr.get("reportTypeMaster") or {}).get("reportName"), fr.get("epsValue"), fr.get("netWorthPerShare"),
                     fr.get("peValue"), fr.get("profitAmount"), docs[0].get("submittedDate") if docs else None, r.get("applicationStatus")))
    rows.sort(key=lambda x: (x[7] or ""))
    print(f"\n{s} cid={com[s]} reports={len(res)} ({time.time()-t:.1f}s)")
    for x in rows: print("   ", x)
print("\nSAMPLE RAW:", json.dumps(res[-1], default=str)[:1500])
print("companies:", len(com))

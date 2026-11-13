"""Bundle docs/index.html + a data folder into ONE self-contained HTML file
(used for the offline demo preview)."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def bundle(index_html: Path, data_dir: Path, out: Path) -> None:
    embed = {}
    for f in data_dir.glob("*.json"):
        embed[f.stem] = json.loads(f.read_text())
    for f in (data_dir / "history").glob("*.json"):
        embed[f"history/{f.stem}"] = json.loads(f.read_text())
    html = index_html.read_text()
    tag = "<script>window.EMBED=" + json.dumps(embed, separators=(",", ":")).replace("</", "<\\/") + ";</script>\n"
    html = html.replace("<script>\n/* ====", tag + "<script>\n/* ====", 1)
    out.write_text(html)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    bundle(root / "docs" / "index.html", Path(sys.argv[1]) if len(sys.argv) > 1 else root / "demo" / "docs" / "data",
           Path(sys.argv[2]) if len(sys.argv) > 2 else root / "demo" / "nepse_screener_demo.html")

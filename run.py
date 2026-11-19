"""python run.py [calc|deck|memo|qa|all]"""
import importlib
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "calc"))

DOMAINS = ["dq", "revenue", "retention", "burn", "channels", "derived"]
COLS = ["id", "slide", "metric", "value", "unit", "period", "definition", "source_file", "filter", "formula", "caveat"]


def calc():
    import load
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "inventory.md").write_text(load.inventory_md())
    load.monthly_totals().to_csv(load.SERIES / "core_mrr_monthly.csv")
    frames = []
    for d in DOMAINS:
        if not (ROOT / "calc" / f"{d}.py").exists():
            print(f"[calc] skip {d} (no module)")
            continue
        mod = importlib.import_module(d)
        rows = mod.run()
        if rows is not None and len(rows):
            rows = rows.reindex(columns=COLS)
            rows["domain"] = d
            frames.append(rows)
        print(f"[calc] {d}: {0 if rows is None else len(rows)} metrics")
    m = pd.concat(frames, ignore_index=True)
    dup = m[m["id"].duplicated(keep=False)]
    if len(dup):
        raise SystemExit(f"duplicate metric ids: {sorted(dup['id'].unique())}")
    m.to_csv(ROOT / "calc" / "metrics.csv", index=False)
    print(f"[calc] metrics.csv: {len(m)} rows")
    if (ROOT / "calc" / "checks.py").exists():
        importlib.import_module("checks").run()
    _script("calc/gate.py")


def _script(path):
    subprocess.run([sys.executable, str(ROOT / path)], check=True)


def deck():
    _script("deck/build.py")


def memo():
    _script("memo/build.py")
    _script("calc/export.py")


def qa():
    _script("qa.py")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {"calc": [calc], "deck": [deck], "memo": [memo], "qa": [qa], "all": [calc, deck, memo, qa]}[stage]
    for s in steps:
        s()

"""out/calc_table.csv: every metric with derivation, plus where it is used (deck slide / memo)."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    m = pd.read_csv(ROOT / "calc" / "metrics.csv")
    used = {}
    for name, f in [("deck", "deck_ids.csv"), ("memo", "memo_ids.csv")]:
        p = ROOT / "out" / f
        if p.exists():
            for i in pd.read_csv(p)["id"]:
                used.setdefault(i, []).append(name)
    m["used_in"] = m["id"].map(lambda i: "+".join(used.get(i, [])))
    m["shown_as"] = ""
    for f in ["deck_ids.csv", "memo_ids.csv"]:
        p = ROOT / "out" / f
        if p.exists():
            d = pd.read_csv(p)
            col = [c for c in d.columns if c != "id"][0]
            m.loc[m["id"].isin(d["id"]), "shown_as"] = m["id"].map(dict(zip(d["id"], d[col].astype(str))))
    m["source"] = m["source_file"].fillna("") + m["filter"].fillna("").map(lambda s: f" | {s}" if s else "")
    m["sort"] = m["used_in"].eq("").astype(int)
    out = m.sort_values(["sort", "slide", "id"], key=lambda s: s.astype(str))[
        ["id", "used_in", "slide", "metric", "value", "shown_as", "unit", "period", "definition", "source", "formula", "caveat"]]
    out.to_csv(ROOT / "out" / "calc_table.csv", index=False)
    missing = [i for i in used if i not in set(m["id"])]
    print(f"calc_table: {len(out)} rows, {sum(m['used_in'] != '')} used in deck/memo; missing ids: {missing}")


if __name__ == "__main__":
    main()

"""Vardgaranti pipeline v1. Fetches Kolada v3 data, writes daily JSON.

Stdlib only. No API key. Loud errors by design: if Kolada changes shape,
the Actions log must show exactly what came back.
"""
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

BASE = "https://api.kolada.se/v3"
KPIS = {"N79221": "forsta_kontakt", "N79223": "operation_atgard"}
HEADERS = {"User-Agent": "loftet-pipeline (civic accountability, open data; github.com/shamathakur77/loftet-pipelin)"}
OUT_DIR = Path("public/data")
YEARS_BACK = 3  # look at the last few years, use the latest that has data


def get_json(url: str):
    print(f"GET {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or "values" not in data:
        print("UNEXPECTED RESPONSE SHAPE, first 500 chars:")
        print(raw[:500])
        raise SystemExit(1)
    return data["values"]


def get_regions():
    entries = get_json(f"{BASE}/municipality")
    regions = {}
    for e in entries:
        if e.get("type") == "L":
            regions[e["id"]] = e.get("title", e["id"])
    if not (15 <= len(regions) <= 25):
        print(f"SANITY FAIL: expected ~21 regions, got {len(regions)}: {sorted(regions.items())}")
        raise SystemExit(1)
    print(f"{len(regions)} regions found")
    return regions


def latest_value(datapoints):
    """datapoints: list of {period, values:[{gender, value, status}]} for one region.
    Returns (year, value, uncertain) for the newest period with a total value."""
    best = None
    for dp in datapoints:
        period = int(str(dp.get("period"))[:4])
        for v in dp.get("values", []):
            if v.get("gender") in (None, "T"):
                val = v.get("value")
                status = v.get("status") or ""
                cand = (period, val, status)
                if best is None or period > best[0]:
                    best = cand
    if best is None:
        return None
    year, val, status = best
    uncertain = (val is None) or (str(status).strip() not in ("", "None"))
    return year, val, uncertain


def fetch_kpi(kpi_id, region_ids):
    years = ",".join(str(y) for y in range(date.today().year - YEARS_BACK, date.today().year + 1))
    ids = ",".join(sorted(region_ids))
    entries = get_json(f"{BASE}/data/kpi/{kpi_id}/municipality/{ids}/year/{years}")
    per_region = {}
    for e in entries:
        rid = e.get("municipality") or e.get("municipality_id")
        if rid is None:
            print(f"WARNING: entry without municipality id: {json.dumps(e)[:300]}")
            continue
        per_region.setdefault(str(rid), []).append(e)
    result = {}
    for rid, dps in per_region.items():
        lv = latest_value(dps)
        if lv:
            result[rid] = lv
    print(f"{kpi_id}: values for {len(result)} regions")
    return result


def people_language(value):
    """61.3 -> '4 av 10 väntade längre än lagen lovar'. Returns None if value missing."""
    if value is None:
        return None
    waited_too_long = round((100 - value) / 10)
    if waited_too_long <= 0:
        return "Färre än 1 av 10 väntade längre än lagen lovar."
    return f"{waited_too_long} av 10 väntade längre än lagen lovar."


def main():
    regions = get_regions()
    data = {k: fetch_kpi(k, regions.keys()) for k in KPIS}

    cards = []
    for rid in sorted(regions):
        entry = {"region_id": rid, "region": regions[rid]}
        for kpi_id, key in KPIS.items():
            got = data[kpi_id].get(rid)
            if got:
                year, val, uncertain = got
                entry[key] = {"year": year, "andel_inom_90": val, "uncertain": uncertain,
                              "gap_text": people_language(val)}
            else:
                entry[key] = {"year": None, "andel_inom_90": None, "uncertain": True, "gap_text": None}
        cards.append(entry)

    with_data = [c for c in cards if c["forsta_kontakt"]["andel_inom_90"] is not None]
    if len(with_data) < 10:
        print(f"SANITY FAIL: only {len(with_data)} regions have N79221 data. Not publishing.")
        raise SystemExit(1)

    today_index = date.today().timetuple().tm_yday % len(cards)
    meta = {
        "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Källa: Väntetider i vården (SKR) via Kolada",
        "promise": "Enligt vårdgarantin ska du få din första kontakt med specialiserad vård inom 90 dagar. Det är lag.",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "all.json").write_text(
        json.dumps({"meta": meta, "regions": cards}, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "today.json").write_text(
        json.dumps({"meta": meta, "card": cards[today_index]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(cards)} regions. Today: {cards[today_index]['region']}")


if __name__ == "__main__":
    sys.exit(main())

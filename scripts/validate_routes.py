"""
Validate routes.json invariants that the scraper/analysis relies on.
"""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
ROUTES_JSON = ROOT / "routes.json"
VALID_CABINS = {"economy", "premium_economy", "business", "first"}


def fail(errors):
    for err in errors:
        print(f"routes.json error: {err}", file=sys.stderr)
    raise SystemExit(1)


def main():
    data = json.loads(ROUTES_JSON.read_text(encoding="utf-8"))
    errors = []
    routes = data.get("routes")
    if not isinstance(routes, list):
        fail(["routes 必須是 list"])

    ids = set()
    for route in routes:
        rid = route.get("id", "?")
        if rid in ids:
            errors.append(f"route id 重複：{rid}")
        ids.add(rid)

        if "return_time_window" in route:
            errors.append(f"#{rid} 不應再使用 return_time_window；目前只支援 depart_time_window")

        cabins = route.get("cabin_classes")
        if not isinstance(cabins, list) or len(cabins) != 1:
            errors.append(f"#{rid} cabin_classes 必須剛好 1 個；多艙等請拆成多條 route")
        elif cabins[0] not in VALID_CABINS:
            errors.append(f"#{rid} 不支援的艙等：{cabins[0]}")

        if not route.get("origin"):
            errors.append(f"#{rid} 缺 origin")
        if not route.get("destinations"):
            errors.append(f"#{rid} 缺 destinations")
        rng = route.get("depart_date_range") or {}
        if not rng.get("start") or not rng.get("end"):
            errors.append(f"#{rid} 缺 depart_date_range.start/end")

    if errors:
        fail(errors)
    print(f"routes.json OK：{len(routes)} 條 route")


if __name__ == "__main__":
    main()

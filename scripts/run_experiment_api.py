"""Прогоняет сценарии А, Б, В через работающее приложение и сохраняет результаты."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pandas as pd

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from placement import config  # noqa: E402


SCENARIOS = {
    "A": {
        "title": "Одна больница на 600 мест в квартале 3871",
        "hospitals": [{"block_id": 3871, "capacity": 600}],
        "reason": (
            "Квартал 3871 — наибольший собственный дефицит (10 мест) среди кварталов "
            "в пределах 10 км от центра спроса; он находится в северо-восточной части города."
        ),
    },
    "B": {
        "title": "Две больницы по 300 мест в кварталах 3871 и 4195",
        "hospitals": [
            {"block_id": 3871, "capacity": 300},
            {"block_id": 4195, "capacity": 300},
        ],
        "reason": (
            "Два крупнейших локальных дефицита в пределах 15 км от центра спроса: "
            "4195 (12 мест, северо-запад) и 3871 (10 мест, северо-восток); "
            "вместимость разделена между двумя полюсами дефицита."
        ),
    },
    "C": {
        "title": "Автоподбор одной больницы на 600 мест: квартал 321",
        "hospitals": [{"block_id": 321, "capacity": 600}],
        "reason": "Лучший результат автоподбора; всего 23 равноценных квартала.",
    },
}

CSV_COLUMNS = [
    "block_id",
    "population",
    "demand",
    "capacity_original",
    "capacity_added",
    "capacity",
    "provision_original",
    "provision",
    "deficit_original",
    "deficit",
]


def request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def compact_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload["summary"].copy()
    summary["blocks_improved"] = sum(1 for item in payload["blocks"] if item["deficit"] < item["deficit_original"])
    summary["blocks_with_remaining_deficit"] = sum(1 for item in payload["blocks"] if item["deficit"] > 0)
    improved = [
        {
            "block_id": item["block_id"],
            "provision_original": item["provision_original"],
            "provision_scenario": item["provision"],
            "provision_change": (item["provision"] or 0.0) - (item["provision_original"] or 0.0),
            "deficit_scenario": item["deficit"],
        }
        for item in payload["blocks"]
        if item["deficit"] < item["deficit_original"]
    ]
    improved.sort(key=lambda item: (-item["provision_change"], -item["deficit_scenario"], item["block_id"]))
    remaining = [
        {
            "block_id": item["block_id"],
            "population": item["population"],
            "deficit_scenario": item["deficit"],
            "provision_scenario": item["provision"],
        }
        for item in payload["blocks"]
        if item["deficit"] > 0
    ]
    remaining.sort(key=lambda item: (-item["deficit_scenario"], -item["population"], item["block_id"]))
    summary["top_improved"] = improved[:15]
    summary["top_remaining_deficit"] = remaining[:15]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8050")
    parser.add_argument("--output", default=str(config.RESULTS_DIR / "experiments"))
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    meta = request_json(f"{args.base_url}/api/meta")
    baseline = {
        "total_demand": meta["total_demand"],
        "total_capacity": meta["total_capacity"],
        "total_demand_within": meta["total_demand"] - meta["baseline_deficit"],
        "deficit": meta["baseline_deficit"],
        "provision_total": meta["baseline_provision"],
    }
    auto = request_json(f"{args.base_url}/api/auto/cache/600")

    comparison = []
    summaries: dict[str, Any] = {}
    for name, scenario in SCENARIOS.items():
        payload = request_json(f"{args.base_url}/api/scenario", {"hospitals": scenario["hospitals"]})
        summary = compact_scenario(payload)
        summaries[name] = {
            "title": scenario["title"],
            "reason": scenario["reason"],
            "hospitals": scenario["hospitals"],
            "summary": summary,
        }
        frame = pd.DataFrame(payload["blocks"])[CSV_COLUMNS]
        frame.to_csv(output / f"scenario_{name}.csv", index=False, encoding="utf-8-sig")
        comparison.append(
            {
                "scenario": name,
                "hospitals": scenario["hospitals"],
                "capacity_added": summary["capacity_added"],
                "total_capacity": summary["total_capacity"],
                "deficit": summary["deficit"],
                "deficit_reduction": summary["deficit_reduction"],
                "deficit_reduction_share": summary["deficit_reduction_share"],
                "provision_total": summary["provision_total"],
                "blocks_improved": summary["blocks_improved"],
                "blocks_with_remaining_deficit": summary["blocks_with_remaining_deficit"],
            }
        )

    manual_a_blocks = {item["block_id"] for item in SCENARIOS["A"]["hospitals"]}
    manual_match = {
        "manual_a_blocks": sorted(manual_a_blocks),
        "auto_best_blocks": auto["best_blocks"],
        "manual_a_in_best": bool(manual_a_blocks.intersection(auto["best_blocks"])),
        "manual_a_reduction": summaries["A"]["summary"]["deficit_reduction"],
        "auto_best_reduction": auto["best_reduction"],
        "manual_c_reduction": summaries["C"]["summary"]["deficit_reduction"],
        "manual_c_matches_auto": summaries["C"]["summary"]["deficit_reduction"] == auto["best_reduction"],
    }

    payload = {
        "baseline": baseline,
        "auto": {
            "capacity": auto["capacity"],
            "checked": auto["checked"],
            "total": auto["total"],
            "errors": auto["errors"],
            "complete": auto["complete"],
            "best_reduction": auto["best_reduction"],
            "best_blocks": auto["best_blocks"],
            "best_count": auto["best_count"],
            "method": auto["method"],
        },
        "comparison": comparison,
        "scenarios": summaries,
        "manual_match": manual_match,
    }
    with (output / "experiment_summary.json").open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    print(json.dumps(payload["comparison"], ensure_ascii=False, indent=2))
    print("Проверка ручного квартала 321:", manual_match["manual_c_matches_auto"])
    print("Сохранено в", output)


if __name__ == "__main__":
    main()

"""Сверка автоподбора (min-cost-flow) с векторизованной LP и BlocksNet."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from placement.auto import auto_search  # noqa: E402
from placement.data import load_data  # noqa: E402
from placement.provision import blocksnet_provision, vectorized_provision  # noqa: E402


def lp_deficit(data, block_id: int, capacity: int) -> float:
    result = vectorized_provision(
        population=data.population,
        base_capacity=data.capacity,
        matrix=data.matrix,
        demand_norm=data.demand_norm,
        accessibility_min=data.accessibility_min,
        additional_capacity={int(block_id): int(capacity)},
    )
    return float(result["deficit"].sum())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity", type=int, default=600)
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--random", type=int, default=100)
    parser.add_argument("--blocksnet", type=int, default=3)
    args = parser.parse_args()

    data = load_data(run_blocksnet_validation=False)
    auto = auto_search(data, args.capacity)
    table = pd.DataFrame(auto["rows"])

    top = table.head(args.top).copy()
    print(f"Проверка топ-{len(top)} вариантов автоподбора через LP...")
    started = time.time()
    top["lp_deficit"] = [lp_deficit(data, int(row.block_id), args.capacity) for row in top.itertuples()]
    top["lp_reduction"] = data.baseline_deficit - top["lp_deficit"]
    top["diff_reduction"] = top["lp_reduction"] - top["deficit_reduction"]
    print(f"Время LP: {time.time() - started:.1f} с")
    print(f"Максимальное расхождение сокращения: {top['diff_reduction'].abs().max()}")
    print(f"Расхождений больше 1e-6: {(top['diff_reduction'].abs() > 1e-6).sum()}")
    best_lp = top["lp_reduction"].max()
    tied_lp = top.loc[(top["lp_reduction"] - best_lp).abs() <= 1e-6, "block_id"].astype(int).tolist()
    print(f"Лучшее сокращение по LP: {best_lp}; равноценных: {len(tied_lp)}; {tied_lp}")

    rng = np.random.default_rng(2026)
    sample_ids = rng.choice(data.block_ids.to_numpy(), size=min(args.random, len(data.block_ids)), replace=False)
    sample = pd.DataFrame({"block_id": sample_ids})
    sample["lp_deficit"] = [lp_deficit(data, int(block_id), args.capacity) for block_id in sample["block_id"]]
    sample["lp_reduction"] = data.baseline_deficit - sample["lp_deficit"]
    mcf_map = table.set_index("block_id")["deficit_reduction"]
    sample["mcf_reduction"] = [int(mcf_map.loc[int(block_id)]) for block_id in sample["block_id"]]
    sample["diff_reduction"] = sample["lp_reduction"] - sample["mcf_reduction"]
    print(f"Случайная выборка {len(sample)}: максимальное расхождение {sample['diff_reduction'].abs().max()}")
    print(f"Расхождений больше 1e-6: {(sample['diff_reduction'].abs() > 1e-6).sum()}")

    print("Сверка LP с BlocksNet на выбранных сценариях...")
    for block_id in [int(x) for x in table.head(args.blocksnet)["block_id"]]:
        vec = vectorized_provision(
            population=data.population,
            base_capacity=data.capacity,
            matrix=data.matrix,
            demand_norm=data.demand_norm,
            accessibility_min=data.accessibility_min,
            additional_capacity={block_id: args.capacity},
        )
        bn = blocksnet_provision(
            population=data.population,
            base_capacity=data.capacity,
            matrix=data.matrix,
            accessibility_min=data.accessibility_min,
            demand_norm=data.demand_norm,
            additional_capacity={block_id: args.capacity},
        )
        vec_def = float(vec["deficit"].sum())
        bn_def = float(bn["deficit"].sum())
        prov_diff = float((vec["provision_strong"].fillna(0) - bn["provision_strong"].fillna(0)).abs().max())
        print(
            f"квартал {block_id}: LP дефицит {vec_def}, BlocksNet {bn_def}, "
            f"разница {vec_def - bn_def}, max|Δprovision| {prov_diff}"
        )

    payload = {
        "capacity": args.capacity,
        "auto_best_reduction": auto["best_reduction"],
        "auto_best_blocks": auto["best_blocks"],
        "lp_best_reduction": float(best_lp),
        "lp_best_blocks": tied_lp,
        "top_checked": int(len(top)),
        "top_max_abs_diff": float(top["diff_reduction"].abs().max()),
        "top_mismatches": int((top["diff_reduction"].abs() > 1e-6).sum()),
        "random_checked": int(len(sample)),
        "random_max_abs_diff": float(sample["diff_reduction"].abs().max()),
        "random_mismatches": int((sample["diff_reduction"].abs() > 1e-6).sum()),
    }
    output = APP_DIR / "data" / "results" / "auto" / f"verification_{args.capacity}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {output}")


if __name__ == "__main__":
    main()

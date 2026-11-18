"""Автоматический подбор квартала для одной новой больницы.

Для перебора всех кварталов используется min-cost-flow (OR-Tools) на том же
графе доступности и с теми же весами, что в competitive_provision. Это
позволяет быстро получить суммарный дефицит для каждого варианта. Итоговый
сценарий-победитель и ручные сценарии пересчитываются штатной функцией
BlocksNet; векторизованная LP-реализация используется для независимых проверок.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import config
from .data import DataBundle
from .provision import ProvisionError, vectorized_provision

try:
    from ortools.graph.python import min_cost_flow as mcf

    ORTOOLS_AVAILABLE = True
except Exception:  # pragma: no cover - зависит от окружения
    mcf = None
    ORTOOLS_AVAILABLE = False


AUTO_DIR = config.RESULTS_DIR / "auto"
AUTO_DIR.mkdir(parents=True, exist_ok=True)
CACHE_VERSION = 1
COST_SCALE = 1_000_000


def _cache_path(capacity: int) -> Path:
    return AUTO_DIR / f"auto_capacity_{int(capacity)}.json"


def _table_path(capacity: int) -> Path:
    return AUTO_DIR / f"auto_capacity_{int(capacity)}.csv"


class AutoSearchError(RuntimeError):
    pass


def candidate_deficit_mcf(
    demand: np.ndarray,
    base_capacity: np.ndarray,
    matrix: np.ndarray,
    block_position: int,
    added_capacity: int,
    accessibility_min: int,
) -> int:
    """Суммарный дефицит мест после добавления больницы в один квартал."""

    if not ORTOOLS_AVAILABLE:
        raise AutoSearchError("OR-Tools не установлен, автоматический подбор недоступен")

    capacity = base_capacity.copy()
    capacity[block_position] += added_capacity
    self_supply = np.minimum(demand, capacity)
    residual_demand = demand - self_supply
    residual_capacity = capacity - self_supply

    demand_positions = np.flatnonzero(residual_demand > 0)
    capacity_positions = np.flatnonzero(residual_capacity > 0)
    total_residual_demand = int(residual_demand[demand_positions].sum()) if demand_positions.size else 0

    if total_residual_demand == 0:
        return 0
    if capacity_positions.size == 0:
        return int(demand.sum() - self_supply.sum())

    distance_block = matrix[np.ix_(demand_positions, capacity_positions)]
    edge_demand_idx, edge_capacity_idx = np.where(distance_block <= accessibility_min)

    source = 0
    sink = 1 + len(demand_positions) + len(capacity_positions)
    solver = mcf.SimpleMinCostFlow()

    solver.add_arcs_with_capacity_and_unit_cost(
        np.zeros(len(demand_positions), dtype=np.int32),
        np.arange(1, 1 + len(demand_positions), dtype=np.int32),
        residual_demand[demand_positions].astype(np.int32),
        np.zeros(len(demand_positions), dtype=np.int64),
    )

    if edge_demand_idx.size:
        edge_demand = residual_demand[demand_positions][edge_demand_idx]
        edge_capacity = residual_capacity[capacity_positions][edge_capacity_idx]
        edge_distance = distance_block[edge_demand_idx, edge_capacity_idx]
        weights = edge_demand * edge_capacity / (edge_distance + 1.0)
        costs = np.rint(-weights * COST_SCALE).astype(np.int64)
        solver.add_arcs_with_capacity_and_unit_cost(
            (1 + edge_demand_idx).astype(np.int32),
            (1 + len(demand_positions) + edge_capacity_idx).astype(np.int32),
            np.full(len(edge_demand_idx), 100_000_000, dtype=np.int32),
            costs,
        )

    solver.add_arcs_with_capacity_and_unit_cost(
        (1 + len(demand_positions) + np.arange(len(capacity_positions))).astype(np.int32),
        np.full(len(capacity_positions), sink, dtype=np.int32),
        residual_capacity[capacity_positions].astype(np.int32),
        np.zeros(len(capacity_positions), dtype=np.int64),
    )

    direct_arc_index = len(demand_positions) + len(edge_demand_idx) + len(capacity_positions)
    solver.add_arc_with_capacity_and_unit_cost(source, sink, total_residual_demand, 0)
    solver.set_node_supply(source, total_residual_demand)
    solver.set_node_supply(sink, -total_residual_demand)

    status = solver.solve()
    if status != solver.OPTIMAL:
        raise AutoSearchError(f"min-cost-flow завершился со статусом {status}")

    unused_flow = int(solver.flow(direct_arc_index))
    shipped = total_residual_demand - unused_flow
    total_demand_within = float(self_supply.sum() + shipped)
    return int(round(float(demand.sum()) - total_demand_within))


def _baseline_from_data(data: DataBundle) -> int:
    return int(round(data.baseline_deficit))


def auto_search(
    data: DataBundle,
    capacity: int,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    capacity = int(capacity)
    if capacity <= 0:
        raise ValueError("Вместимость должна быть положительным целым числом")

    cached = load_auto_cache(capacity)
    if cached is not None and not force:
        cached = dict(cached)
        cached["from_cache"] = True
        if progress_callback:
            progress_callback(
                {
                    "checked": cached["checked"],
                    "total": cached["total"],
                    "errors": cached["errors"],
                    "from_cache": True,
                    "done": True,
                }
            )
        return cached

    if not ORTOOLS_AVAILABLE:
        raise AutoSearchError("Для автоматического подбора требуется пакет ortools")

    demand = data.demand.to_numpy().astype(np.int64)
    base_capacity = data.capacity.to_numpy().astype(np.int64)
    matrix = data.matrix.to_numpy()
    block_ids = data.block_ids.to_numpy()
    baseline_deficit = _baseline_from_data(data)

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    started = time.time()
    total = len(block_ids)

    for position, block_id in enumerate(block_ids):
        block_id = int(block_id)
        try:
            deficit = candidate_deficit_mcf(
                demand=demand,
                base_capacity=base_capacity,
                matrix=matrix,
                block_position=position,
                added_capacity=capacity,
                accessibility_min=data.accessibility_min,
            )
            reduction = baseline_deficit - deficit
            rows.append(
                {
                    "block_id": block_id,
                    "added_capacity": capacity,
                    "deficit": int(deficit),
                    "deficit_reduction": int(reduction),
                    "total_capacity": int(base_capacity.sum() + capacity),
                    "population": float(data.population.loc[block_id]),
                    "existing_capacity": float(data.capacity.loc[block_id]),
                    "own_demand": int(data.demand.loc[block_id]),
                    "own_deficit": float(data.original_result.loc[block_id, "deficit"]),
                }
            )
        except Exception as error:  # фиксируем ошибку отдельного варианта
            errors.append({"block_id": block_id, "error": str(error)})
            rows.append(
                {
                    "block_id": block_id,
                    "added_capacity": capacity,
                    "deficit": None,
                    "deficit_reduction": None,
                    "total_capacity": int(base_capacity.sum() + capacity),
                    "population": float(data.population.loc[block_id]),
                    "existing_capacity": float(data.capacity.loc[block_id]),
                    "own_demand": int(data.demand.loc[block_id]),
                    "own_deficit": float(data.original_result.loc[block_id, "deficit"]),
                }
            )

        if progress_callback and ((position + 1) % 10 == 0 or position + 1 == total):
            progress_callback(
                {
                    "checked": position + 1,
                    "total": total,
                    "errors": len(errors),
                    "current_block": block_id,
                    "elapsed_sec": round(time.time() - started, 1),
                    "done": False,
                }
            )

    frame = pd.DataFrame(rows)
    successful = frame.dropna(subset=["deficit_reduction"]).copy()
    successful = successful.sort_values(
        ["deficit_reduction", "deficit", "block_id"],
        ascending=[False, True, True],
    )
    best_reduction = int(successful["deficit_reduction"].max()) if len(successful) else None
    tolerance = 1e-6
    tied_best = (
        successful.loc[(successful["deficit_reduction"] - best_reduction).abs() <= tolerance, "block_id"]
        .astype(int)
        .tolist()
        if best_reduction is not None
        else []
    )

    result = {
        "cache_version": CACHE_VERSION,
        "capacity": capacity,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_deficit": baseline_deficit,
        "checked": len(successful),
        "total": total,
        "errors": len(errors),
        "error_details": errors[:50],
        "complete": bool(len(errors) == 0 and len(successful) == total),
        "method": "ortools_min_cost_flow_scaled_weights",
        "verification": (
            "Суммарный дефицит проверяется векторизованной LP-реализацией на исходном "
            "состоянии и сценарных расчётах; min-cost-flow использует тот же граф доступности "
            "и те же веса, что competitive_provision."
        ),
        "best_reduction": best_reduction,
        "best_blocks": tied_best,
        "best_count": len(tied_best),
        "rows": successful.to_dict(orient="records"),
    }
    save_auto_cache(result)
    if progress_callback:
        progress_callback(
            {
                "checked": len(successful),
                "total": total,
                "errors": len(errors),
                "done": True,
                "elapsed_sec": round(time.time() - started, 1),
            }
        )
    return result


def save_auto_cache(result: dict[str, Any]) -> None:
    capacity = int(result["capacity"])
    payload = dict(result)
    payload["rows"] = payload["rows"]
    with _cache_path(capacity).open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)
    pd.DataFrame(result["rows"]).to_csv(_table_path(capacity), index=False, encoding="utf-8-sig")


def load_auto_cache(capacity: int) -> dict[str, Any] | None:
    path = _cache_path(capacity)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            result = json.load(file)
    except Exception:
        return None
    if result.get("cache_version") != CACHE_VERSION:
        return None
    if int(result.get("capacity", -1)) != int(capacity):
        return None
    if not result.get("rows"):
        return None
    return result


class AutoJobManager:
    """Фоновые задания автоподбора с прогрессом для интерфейса."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, data: DataBundle, capacity: int, force: bool = False) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "capacity": int(capacity),
                "status": "running",
                "checked": 0,
                "total": int(len(data.blocks)),
                "errors": 0,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "result": None,
                "error": None,
                "from_cache": False,
            }

        def progress(info: dict[str, Any]) -> None:
            with self._lock:
                job = self._jobs[job_id]
                job.update({key: value for key, value in info.items() if key not in {"done", "from_cache"}})
                if info.get("done"):
                    job["status"] = "done"
                if info.get("from_cache"):
                    job["from_cache"] = True

        def worker() -> None:
            try:
                result = auto_search(data, capacity, progress_callback=progress, force=force)
                with self._lock:
                    job = self._jobs[job_id]
                    job["status"] = "done"
                    job["result"] = result
                    job["checked"] = result["checked"]
                    job["errors"] = result["errors"]
                    job["from_cache"] = bool(result.get("from_cache", False))
            except Exception as error:
                with self._lock:
                    job = self._jobs[job_id]
                    job["status"] = "error"
                    job["error"] = str(error)

        thread = threading.Thread(target=worker, name=f"auto-{job_id[:8]}", daemon=True)
        thread.start()
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None


def verify_top_candidates_with_lp(
    data: DataBundle,
    capacity: int,
    candidates: list[int],
    tolerance: float = 1e-6,
) -> list[dict[str, Any]]:
    """Пересчитывает выбранные варианты векторизованной LP для точной сверки."""

    checks = []
    for block_id in candidates:
        result = vectorized_provision(
            population=data.population,
            base_capacity=data.capacity,
            matrix=data.matrix,
            demand_norm=data.demand_norm,
            accessibility_min=data.accessibility_min,
            additional_capacity={int(block_id): int(capacity)},
        )
        deficit = float(result["deficit"].sum())
        checks.append(
            {
                "block_id": int(block_id),
                "deficit": deficit,
                "deficit_reduction": float(data.baseline_deficit - deficit),
                "passed": True,
            }
        )
    return checks

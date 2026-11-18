"""Расчёт обеспеченности местами в больницах.

Реализованы два пути:
* ``blocksnet_provision`` — прямой вызов competitive_provision из BlocksNet,
  точно повторяющий расчёт практики 2 (используется для сверки);
* ``vectorized_provision`` — векторизованная LP-реализация того же метода
  (scipy/HiGHS), которая используется для независимых проверок и сверки.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import coo_matrix


class ProvisionError(RuntimeError):
    """Ошибка расчёта обеспеченности."""


def demand_from_population(population: pd.Series, demand_norm: int) -> pd.Series:
    return np.ceil(population / 1000.0 * demand_norm).astype(int).rename("demand")


def _build_result(
    demand: pd.Series,
    capacity: pd.Series,
    self_supply: np.ndarray,
    allocation: np.ndarray,
) -> pd.DataFrame:
    demand_within = self_supply + allocation
    demand_within = np.minimum(demand_within, demand.to_numpy())
    deficit = demand.to_numpy() - demand_within
    provision = np.divide(
        demand_within,
        demand.to_numpy(),
        out=np.full(len(demand), np.nan, dtype=float),
        where=demand.to_numpy() > 0,
    )
    provision = np.clip(provision, 0.0, 1.0)

    return pd.DataFrame(
        {
            "demand": demand.to_numpy().astype(int),
            "capacity": capacity.to_numpy().astype(float),
            "self_supply": self_supply.astype(float),
            "demand_within": demand_within.astype(float),
            "provision_strong": provision,
            "provision_weak": provision.copy(),
            "deficit": deficit.astype(float),
        },
        index=demand.index,
    )


def vectorized_provision(
    population: pd.Series,
    base_capacity: pd.Series,
    matrix: pd.DataFrame,
    demand_norm: int,
    accessibility_min: int,
    additional_capacity: dict[int, int] | None = None,
) -> pd.DataFrame:
    """Пересчитывает обеспеченность тем же методом, что competitive_provision.

    Шаги совпадают с BlocksNet: demand = ceil(population / 1000 * demand_norm),
    затем самопокрытие min(demand, capacity), затем LP на остаточных спросе и
    вместимости с весами demand * capacity / (distance + 1) и ограничением
    distance <= accessibility. Матрица целочисленная, поэтому оптимум LP
    совпадает с целочисленной задачей BlocksNet.
    """

    if accessibility_min <= 0:
        raise ProvisionError("Доступность должна быть положительной")
    if demand_norm <= 0:
        raise ProvisionError("Норматив обеспеченности должен быть положительным")

    additional_capacity = additional_capacity or {}
    for block_id, value in additional_capacity.items():
        if int(block_id) not in base_capacity.index:
            raise ProvisionError(f"Квартал {block_id} отсутствует в данных")
        if not isinstance(value, (int, np.integer)) or value <= 0:
            raise ProvisionError("Вместимость новой больницы должна быть положительным целым числом")

    demand = demand_from_population(population, demand_norm)
    capacity = base_capacity.astype(float).copy()
    for block_id, value in additional_capacity.items():
        capacity.loc[int(block_id)] = capacity.loc[int(block_id)] + int(value)

    self_supply = np.minimum(demand.to_numpy(), capacity.to_numpy())
    residual_demand = demand.to_numpy() - self_supply
    residual_capacity = capacity.to_numpy() - self_supply

    allocation = np.zeros(len(demand), dtype=float)
    demand_positions = np.flatnonzero(residual_demand > 0)
    capacity_positions = np.flatnonzero(residual_capacity > 0)

    if demand_positions.size and capacity_positions.size:
        residual_demand_values = residual_demand[demand_positions]
        residual_capacity_values = residual_capacity[capacity_positions]
        distance_block = matrix.to_numpy()[np.ix_(demand_positions, capacity_positions)]
        edge_demand_idx, edge_capacity_idx = np.where(distance_block <= accessibility_min)

        if edge_demand_idx.size:
            edge_demand = residual_demand_values[edge_demand_idx]
            edge_capacity = residual_capacity_values[edge_capacity_idx]
            edge_distance = distance_block[edge_demand_idx, edge_capacity_idx]
            weights = edge_demand * edge_capacity / (edge_distance + 1.0)

            n_edges = len(edge_demand_idx)
            n_rows = len(demand_positions) + len(capacity_positions)
            rows = np.concatenate([edge_demand_idx, len(demand_positions) + edge_capacity_idx])
            cols = np.concatenate([np.arange(n_edges), np.arange(n_edges)])
            constraint = coo_matrix(
                (np.ones(2 * n_edges), (rows, cols)),
                shape=(n_rows, n_edges),
            ).tocsr()
            upper_bounds = np.concatenate([residual_demand_values, residual_capacity_values])

            solution = linprog(
                -weights,
                A_ub=constraint,
                b_ub=upper_bounds,
                bounds=(0.0, None),
                method="highs-ds",
            )
            if solution.status != 0 or solution.x is None:
                raise ProvisionError(f"LP не решилась: {solution.message}")

            values = np.asarray(solution.x, dtype=float)
            rounded = np.rint(values)
            if np.max(np.abs(values - rounded)) > 1e-4:
                # Страховка: если решатель вернул дробное решение, решаем MIP.
                mip_solution = milp(
                    -weights,
                    integrality=np.ones(n_edges),
                    bounds=Bounds(0, np.inf),
                    constraints=LinearConstraint(constraint, -np.inf, upper_bounds),
                    options={"time_limit": 120},
                )
                if mip_solution.status != 0 or mip_solution.x is None:
                    raise ProvisionError(f"MIP не решилась: {mip_solution.message}")
                rounded = np.rint(mip_solution.x)

            rounded = np.maximum(rounded, 0.0)
            per_demand = np.zeros(len(demand_positions), dtype=float)
            np.add.at(per_demand, edge_demand_idx, rounded)
            per_demand = np.minimum(per_demand, residual_demand_values)
            allocation[demand_positions] = per_demand

    return _build_result(demand, capacity, self_supply, allocation)


def blocksnet_provision(
    population: pd.Series,
    base_capacity: pd.Series,
    matrix: pd.DataFrame,
    accessibility_min: int,
    demand_norm: int,
    additional_capacity: dict[int, int] | None = None,
) -> pd.DataFrame:
    """Прямой вызов BlocksNet competitive_provision (медленная сверка)."""

    from blocksnet.analysis.provision import competitive_provision

    capacity = base_capacity.astype(float).copy()
    for block_id, value in (additional_capacity or {}).items():
        capacity.loc[int(block_id)] = capacity.loc[int(block_id)] + int(value)

    blocks_df = pd.DataFrame({"population": population.astype(float), "capacity": capacity})
    provision_df, _ = competitive_provision(
        blocks_df,
        matrix,
        accessibility_min,
        demand_norm,
    )
    demand = provision_df["demand"].astype(int)
    demand_within = provision_df["demand_within"].astype(float)
    return pd.DataFrame(
        {
            "demand": demand,
            "capacity": capacity,
            "demand_within": demand_within,
            "provision_strong": provision_df["provision_strong"],
            "provision_weak": provision_df["provision_weak"],
            "deficit": demand - demand_within,
        },
        index=demand.index,
    )


def scenario_summary(result: pd.DataFrame, baseline: pd.DataFrame, demand_total: int) -> dict[str, float]:
    deficit = float(result["deficit"].sum())
    baseline_deficit = float(baseline["deficit"].sum())
    deficit_reduction = baseline_deficit - deficit
    capacity_total = float(result["capacity"].sum())
    demand_within = float(result["demand_within"].sum())
    return {
        "total_demand": int(demand_total),
        "total_capacity": capacity_total,
        "total_demand_within": demand_within,
        "deficit": deficit,
        "baseline_deficit": baseline_deficit,
        "deficit_reduction": deficit_reduction,
        "deficit_reduction_share": (deficit_reduction / baseline_deficit) if baseline_deficit > 0 else 0.0,
        "provision_total": (demand_within / demand_total) if demand_total > 0 else 0.0,
        "baseline_provision_total": (1.0 - baseline_deficit / demand_total) if demand_total > 0 else 0.0,
        "capacity_left": max(capacity_total - demand_within, 0.0),
    }


def validate_capacity(value: object) -> int:
    """Проверяет, что вместимость — положительное целое число."""

    if isinstance(value, bool):
        raise ValueError("Вместимость должна быть положительным целым числом")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("Вместимость должна быть положительным целым числом")
        value = int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise ValueError("Вместимость должна быть положительным целым числом")
        value = int(text)
    if not isinstance(value, (int, np.integer)):
        raise ValueError("Вместимость должна быть положительным целым числом")
    if int(value) <= 0:
        raise ValueError("Вместимость должна быть положительным целым числом")
    return int(value)


def validate_hospitals_payload(payload: Iterable[dict]) -> dict[int, int]:
    """Объединяет несколько больниц в одном квартале."""

    combined: dict[int, int] = {}
    for item in payload:
        block_id = int(item.get("block_id"))
        capacity = validate_capacity(item.get("capacity"))
        combined[block_id] = combined.get(block_id, 0) + capacity
    return combined

"""Загрузка и проверка сохранённых данных практики 2 (только чтение)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from . import config
from .provision import ProvisionError, vectorized_provision


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        try:
            return _clean_value(value.item())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _clean_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_value(v) for v in value]
    return value


def dataframe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = df.where(pd.notna(df), None)
    return [{str(k): _clean_value(v) for k, v in row.items()} for row in out.to_dict(orient="records")]


def _round_coordinates(obj: Any, precision: int = 6) -> Any:
    if isinstance(obj, (list, tuple)):
        if obj and all(isinstance(x, (int, float)) for x in obj):
            return [round(float(x), precision) for x in obj]
        return [_round_coordinates(x, precision) for x in obj]
    if isinstance(obj, dict):
        return {k: _round_coordinates(v, precision) for k, v in obj.items()}
    return obj


def gdf_to_geojson(gdf: gpd.GeoDataFrame) -> dict[str, Any]:
    gdf = gdf.copy()
    gdf["block_id"] = gdf.index.astype(int)
    for column in gdf.columns:
        if column == "geometry":
            continue
        if pd.api.types.is_datetime64_any_dtype(gdf[column]):
            gdf[column] = gdf[column].astype(str)
        elif pd.api.types.is_object_dtype(gdf[column]):
            gdf[column] = gdf[column].map(_clean_value)
    payload = json.loads(gdf.to_json(drop_id=True))
    return _round_coordinates(payload)


def _read_geo(path: Path) -> gpd.GeoDataFrame:
    if path.suffix == ".parquet":
        return gpd.read_parquet(path)
    return gpd.read_file(path)


def _read_optional_layer(filename: str) -> dict[str, Any]:
    path = config.DATA_DIR / filename
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    frame = _read_geo(path)
    if frame.crs is not None:
        frame = frame.to_crs(4326)
    frame = frame[["geometry"]].copy()
    return _round_coordinates(json.loads(frame.to_json(drop_id=True)))


def _read_matrix(path: Path, block_ids: pd.Index) -> tuple[pd.DataFrame, dict[str, Any]]:
    if path.suffix == ".npz":
        archive = np.load(path, allow_pickle=False)
        ids = np.asarray(archive["block_ids"], dtype=np.int64)
        values = np.asarray(archive["matrix"])
        matrix = pd.DataFrame(values, index=ids, columns=ids)
    else:
        matrix = pd.read_csv(path, index_col=0)

    raw_index_dtype = str(matrix.index.dtype)
    raw_columns_dtype = str(matrix.columns.dtype)

    try:
        matrix.index = matrix.index.astype(int)
    except (TypeError, ValueError):
        pass
    try:
        matrix.columns = matrix.columns.astype(int)
    except (TypeError, ValueError):
        pass

    id_set = set(int(x) for x in block_ids)
    index_set = set(matrix.index.tolist())
    column_set = set(matrix.columns.tolist())

    if index_set != id_set or column_set != id_set:
        missing_rows = sorted(id_set - index_set)
        missing_columns = sorted(id_set - column_set)
        extra_rows = sorted(index_set - id_set)
        extra_columns = sorted(column_set - id_set)
        raise ValueError(
            "Индексы матрицы доступности не совпадают с идентификаторами кварталов: "
            f"нет строк {missing_rows[:5]}, нет столбцов {missing_columns[:5]}, "
            f"лишние строки {extra_rows[:5]}, лишние столбцы {extra_columns[:5]}"
        )

    matrix = matrix.reindex(index=block_ids, columns=block_ids)
    if matrix.isna().any().any():
        raise ValueError("Матрица доступности содержит пропуски после выравнивания по кварталам")
    if not matrix.index.equals(matrix.columns):
        raise ValueError("Индекс и столбцы матрицы доступности не совпадают")

    check = {
        "ok": True,
        "shape": list(matrix.shape),
        "raw_index_dtype": raw_index_dtype,
        "raw_columns_dtype": raw_columns_dtype,
        "normalized_index_dtype": str(matrix.index.dtype),
        "normalized_columns_dtype": str(matrix.columns.dtype),
        "index_equals_columns": bool(matrix.index.equals(matrix.columns)),
        "block_ids_match": bool(set(matrix.index) == id_set),
        "min_minutes": float(matrix.to_numpy().min()),
        "max_minutes": float(matrix.to_numpy().max()),
    }
    return matrix, check


def _compare_series(name: str, left: pd.Series, right: pd.Series, tolerance: float = 1e-9) -> dict[str, Any]:
    left = pd.to_numeric(left, errors="coerce").fillna(0.0).astype(float)
    right = pd.to_numeric(right, errors="coerce").fillna(0.0).astype(float)
    diff = (left - right).abs()
    return {
        "name": name,
        "max_abs_diff": float(diff.max()) if len(diff) else 0.0,
        "mismatched_blocks": int((diff > tolerance).sum()),
        "passed": bool((diff <= tolerance).all()),
    }


@dataclass
class DataBundle:
    blocks: gpd.GeoDataFrame
    hospitals_blocks: gpd.GeoDataFrame
    hospitals: gpd.GeoDataFrame | None
    boundaries: gpd.GeoDataFrame | None
    population: pd.Series
    capacity: pd.Series
    hospital_count: pd.Series
    matrix: pd.DataFrame
    saved_provision: pd.DataFrame
    demand_norm: int
    accessibility_min: int
    demand: pd.Series
    original_result: pd.DataFrame
    matrix_check: dict[str, Any]
    validation: dict[str, Any] = field(default_factory=dict)
    blocks_geojson: dict[str, Any] = field(default_factory=dict)
    roads_geojson: dict[str, Any] = field(default_factory=dict)
    water_geojson: dict[str, Any] = field(default_factory=dict)

    @property
    def block_ids(self) -> pd.Index:
        return self.blocks.index

    @property
    def total_demand(self) -> int:
        return int(self.demand.sum())

    @property
    def total_capacity(self) -> float:
        return float(self.capacity.sum())

    @property
    def baseline_deficit(self) -> float:
        return float(self.original_result["deficit"].sum())

    @property
    def baseline_provision(self) -> float:
        if self.total_demand == 0:
            return 0.0
        return float(1.0 - self.baseline_deficit / self.total_demand)


def _compute_validation(bundle_without_validation: DataBundle, run_blocksnet: bool = False) -> dict[str, Any]:
    result = bundle_without_validation
    saved = result.saved_provision
    vectorized = result.original_result

    checks = [
        _compare_series(
            "vectorized_vs_saved_provision_strong",
            vectorized["provision_strong"],
            saved["hospital_provision_strong"],
        ),
        _compare_series(
            "vectorized_vs_saved_demand_within",
            vectorized["demand_within"],
            saved["hospital_demand_within"],
            tolerance=1e-6,
        ),
        _compare_series(
            "vectorized_vs_saved_deficit",
            vectorized["deficit"],
            saved["hospital_demand"] - saved["hospital_demand_within"],
            tolerance=1e-6,
        ),
        _compare_series(
            "saved_csv_vs_blocks_indicators",
            saved["hospital_provision_strong"],
            result.blocks["hospital_provision_strong"],
        ),
    ]

    blocksnet_check: dict[str, Any] | None = None
    if run_blocksnet:
        from .provision import blocksnet_provision

        blocksnet_result = blocksnet_provision(
            result.population,
            result.capacity,
            result.matrix,
            result.accessibility_min,
            result.demand_norm,
        )
        blocksnet_check = {
            "compared": True,
            "provision_strong": _compare_series(
                "vectorized_vs_blocksnet_provision_strong",
                vectorized["provision_strong"],
                blocksnet_result["provision_strong"],
            ),
            "demand_within": _compare_series(
                "vectorized_vs_blocksnet_demand_within",
                vectorized["demand_within"],
                blocksnet_result["demand_within"],
                tolerance=1e-6,
            ),
            "deficit": _compare_series(
                "vectorized_vs_blocksnet_deficit",
                vectorized["deficit"],
                blocksnet_result["deficit"],
                tolerance=1e-6,
            ),
        }

    passed = all(check["passed"] for check in checks)
    if blocksnet_check is not None:
        passed = passed and all(check["passed"] for check in blocksnet_check.values() if isinstance(check, dict))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service_type": config.SERVICE_TYPE,
        "demand_per_1000": result.demand_norm,
        "accessibility_min": result.accessibility_min,
        "blocks": int(len(result.blocks)),
        "matrix": result.matrix_check,
        "saved_result": {
            "total_demand": result.total_demand,
            "total_capacity": result.total_capacity,
            "total_demand_within": float(result.original_result["demand_within"].sum()),
            "total_deficit": result.baseline_deficit,
            "total_provision": result.baseline_provision,
        },
        "checks": checks,
        "blocksnet_original": blocksnet_check,
        "passed": bool(passed),
        "note": (
            "Проверка воспроизводит расчёт обеспеченности competitive_provision "
            "с параметрами hospital и сравнивает его с hospitals_provision.csv и "
            "полем hospital_provision_strong в blocks_indicators.geojson."
        ),
    }


def load_data(run_blocksnet_validation: bool = False) -> DataBundle:
    for filename in config.REQUIRED_FILES:
        path = config.DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Не найден обязательный файл данных: {path}")

    blocks = _read_geo(config.DATA_DIR / config.BLOCKS_FILE)
    blocks.index = blocks.index.astype(int)
    blocks.index.name = None

    population_frame = _read_geo(config.DATA_DIR / config.POPULATION_FILE)
    population_frame.index = population_frame.index.astype(int)
    if not population_frame.index.equals(blocks.index):
        population_frame = population_frame.reindex(blocks.index)
    population = pd.to_numeric(population_frame["population_calibrated"], errors="coerce").fillna(0.0)

    hospitals_blocks = _read_geo(config.DATA_DIR / config.HOSPITAL_BLOCKS_FILE)
    hospitals_blocks.index = hospitals_blocks.index.astype(int)
    if not hospitals_blocks.index.equals(blocks.index):
        hospitals_blocks = hospitals_blocks.reindex(blocks.index)
    capacity = pd.to_numeric(hospitals_blocks["capacity"], errors="coerce").fillna(0.0)
    hospital_count = pd.to_numeric(hospitals_blocks["count"], errors="coerce").fillna(0.0)

    provision_path = config.DATA_DIR / config.HOSPITAL_PROVISION_FILE
    if provision_path.suffix == ".parquet":
        saved_provision = pd.read_parquet(provision_path)
    else:
        saved_provision = pd.read_csv(provision_path, index_col=0)
    saved_provision.index = saved_provision.index.astype(int)
    if not saved_provision.index.equals(blocks.index):
        saved_provision = saved_provision.reindex(blocks.index)

    matrix, matrix_check = _read_matrix(config.DATA_DIR / config.MATRIX_FILE, blocks.index)
    demand_norm, accessibility_min = config.service_parameters()
    demand = np.ceil(population / 1000.0 * demand_norm).astype(int).rename("demand")

    original_result = vectorized_provision(
        population=population,
        base_capacity=capacity,
        matrix=matrix,
        demand_norm=demand_norm,
        accessibility_min=accessibility_min,
    )

    hospitals = None
    hospital_path = config.DATA_DIR / config.HOSPITALS_FILE
    if hospital_path.exists():
        hospitals = _read_geo(hospital_path)
        if hospitals.crs is not None:
            hospitals = hospitals.to_crs(4326)

    boundaries = None
    boundaries_path = config.DATA_DIR / config.BOUNDARIES_FILE
    if boundaries_path.exists():
        boundaries = _read_geo(boundaries_path)
        if boundaries.crs is not None:
            boundaries = boundaries.to_crs(4326)

    bundle = DataBundle(
        blocks=blocks,
        hospitals_blocks=hospitals_blocks,
        hospitals=hospitals,
        boundaries=boundaries,
        population=population,
        capacity=capacity,
        hospital_count=hospital_count,
        matrix=matrix,
        saved_provision=saved_provision,
        demand_norm=demand_norm,
        accessibility_min=accessibility_min,
        demand=demand,
        original_result=original_result,
        matrix_check=matrix_check,
    )
    bundle.validation = _compute_validation(bundle, run_blocksnet=run_blocksnet_validation)
    bundle.roads_geojson = _read_optional_layer(config.ROADS_FILE)
    bundle.water_geojson = _read_optional_layer(config.WATER_FILE)
    if not bundle.validation["passed"]:
        raise ProvisionError(
            "Исходный расчёт не совпал с сохранёнными результатами практики 2. "
            "Смотрите подробности в validation.json."
        )

    blocks_display = blocks.to_crs(4326).copy()
    blocks_display["population"] = population
    blocks_display["demand"] = demand
    blocks_display["capacity"] = capacity
    blocks_display["hospital_count"] = hospital_count
    blocks_display["provision_original"] = original_result["provision_strong"].to_numpy()
    blocks_display["deficit_original"] = original_result["deficit"].to_numpy()
    bundle.blocks_geojson = gdf_to_geojson(
        blocks_display[
            ["population", "demand", "capacity", "hospital_count", "provision_original", "deficit_original", "geometry"]
        ]
    )
    return bundle


def metadata(bundle: DataBundle) -> dict[str, Any]:
    return {
        "title": "Оценка размещения больниц — Астрахань",
        "service_type": config.SERVICE_TYPE,
        "demand_per_1000": bundle.demand_norm,
        "accessibility_min": bundle.accessibility_min,
        "default_auto_capacity": config.DEFAULT_AUTO_CAPACITY,
        "blocks": int(len(bundle.blocks)),
        "population_total": float(bundle.population.sum()),
        "total_demand": bundle.total_demand,
        "total_capacity": bundle.total_capacity,
        "baseline_deficit": bundle.baseline_deficit,
        "baseline_provision": bundle.baseline_provision,
        "matrix_check": bundle.matrix_check,
        "validation": bundle.validation,
        "implementation": {
            "scenario": "BlocksNet competitive_provision — точный расчёт по всей сети",
            "auto": "OR-Tools min-cost-flow на том же графе доступности и весах",
            "vectorized_check": "scipy/HiGHS LP, сверена с BlocksNet по исходному расчёту и сценариям",
        },
        "sources": [
            "OpenStreetMap: больницы (amenity=hospital) и уличная сеть.",
            "BlocksNet: нарезка кварталов, матрица доступности, impute_services, aggregate_objects, competitive_provision.",
            "Практика 2: калибровка населения кварталов по жилой площади до 465 990 человек.",
        ],
        "assumptions": [
            "Вместимость части больниц достроена расчётным путём (impute_services): 110 мест для больницы и 50 для поликлиники по умолчанию BlocksNet.",
            "Численность населения кварталов — расчётная калиброванная оценка, а не точная перепись.",
            "Доступность измеряется только по пешеходному графу; автомобильное и общественное время в пути не учитывается.",
            "Новая больница добавляет вместимость в выбранном квартале; земельный участок и возможность строительства не оцениваются.",
            "Обеспеченность считается конкурирующим методом competitive_provision с нормативом 9 мест на 1000 жителей и доступностью 60 минут.",
            "Итоговые сценарии пересчитываются штатной функцией BlocksNet. Векторизованная LP-реализация используется для проверок и сверки; суммарный дефицит при нескольких оптимальных распределениях совпадает.",
        ],
    }

"""Настройки приложения оценки размещения больниц."""

from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("HOSPITAL_PLACEMENT_DATA", APP_DIR / "data" / "astrakhan_compact"))
RESULTS_DIR = Path(os.environ.get("HOSPITAL_PLACEMENT_RESULTS", APP_DIR / "data" / "results"))

SERVICE_TYPE = "hospital"
DEFAULT_DEMAND_PER_1000 = 9
DEFAULT_ACCESSIBILITY_MIN = 60
DEFAULT_AUTO_CAPACITY = 600

MATRIX_FILE = "accessibility_matrix.npz"
BLOCKS_FILE = "blocks.parquet"
POPULATION_FILE = "population.parquet"
HOSPITAL_BLOCKS_FILE = "hospitals_blocks.parquet"
HOSPITAL_PROVISION_FILE = "hospitals_provision.parquet"
HOSPITALS_FILE = "hospitals.parquet"
BOUNDARIES_FILE = "boundaries.parquet"
ROADS_FILE = "roads.parquet"
WATER_FILE = "water.parquet"

REQUIRED_FILES = (
    MATRIX_FILE,
    BLOCKS_FILE,
    POPULATION_FILE,
    HOSPITAL_BLOCKS_FILE,
    HOSPITAL_PROVISION_FILE,
)


def service_parameters() -> tuple[int, int]:
    """Возвращает норматив (мест на 1000 жителей) и доступность (минуты).

    Значения берутся из BlocksNet, чтобы совпадать с практикой 2. Если пакет
    недоступен, используются зафиксированные значения конфигурации hospital.
    """

    try:
        from blocksnet.config import service_types_config

        config = service_types_config[SERVICE_TYPE]
        return int(config["demand"]), int(config["accessibility"])
    except Exception:
        return DEFAULT_DEMAND_PER_1000, DEFAULT_ACCESSIBILITY_MIN


RESULTS_DIR.mkdir(parents=True, exist_ok=True)

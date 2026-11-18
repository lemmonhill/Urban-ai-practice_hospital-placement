"""Собирает компактный набор данных для деплоя.

Матрица доступности сохраняется в NPZ как float32, таблицы и геометрии — в
GeoParquet/Parquet со сжатием zstd. Исходные файлы практики 2 при этом только
читаются.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = APP_DIR.parent / "python-package-boilerplate" / "examples" / "data" / "astrakhan"
DEFAULT_OUTPUT = APP_DIR / "data" / "astrakhan_compact"


def read_blocks(source: Path) -> gpd.GeoDataFrame:
    blocks = gpd.read_file(source / "blocks_indicators.geojson")
    blocks.index = blocks.index.astype(int)
    blocks.index.name = "block_id"
    return blocks


def build(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    blocks = read_blocks(source)
    block_ids = np.asarray(blocks.index, dtype=np.int32)

    matrix = pd.read_csv(source / "accessibility_matrix.csv", index_col=0)
    matrix.index = matrix.index.astype(int)
    matrix.columns = matrix.columns.astype(int)
    matrix = matrix.reindex(index=blocks.index, columns=blocks.index)
    if matrix.isna().any().any():
        raise ValueError("Матрица доступности не выровнена по кварталам")
    np.savez_compressed(
        output / "accessibility_matrix.npz",
        block_ids=block_ids,
        matrix=matrix.to_numpy(dtype=np.float32),
    )

    blocks[["hospital_provision_strong", "geometry"]].to_parquet(
        output / "blocks.parquet", compression="zstd", index=True
    )

    population = gpd.read_file(source / "buildings_blocks.geojson")
    population.index = population.index.astype(int)
    population.index.name = "block_id"
    population[["population_calibrated", "geometry"]].to_parquet(
        output / "population.parquet", compression="zstd", index=True
    )

    hospitals_blocks = gpd.read_file(source / "hospitals_blocks.geojson")
    hospitals_blocks.index = hospitals_blocks.index.astype(int)
    hospitals_blocks.index.name = "block_id"
    hospitals_blocks[["capacity", "count", "geometry"]].to_parquet(
        output / "hospitals_blocks.parquet", compression="zstd", index=True
    )

    hospitals = gpd.read_file(source / "hospitals.geojson")
    hospitals[["capacity", "geometry"]].to_parquet(
        output / "hospitals.parquet", compression="zstd", index=False
    )

    boundaries = gpd.read_file(source / "boundaries.geojson")
    boundaries[["geometry"]].to_parquet(output / "boundaries.parquet", compression="zstd", index=False)

    for filename, name in (("roads.geojson", "roads.parquet"), ("water.geojson", "water.parquet")):
        layer = gpd.read_file(source / filename)
        layer[["geometry"]].to_parquet(output / name, compression="zstd", index=False)

    provision = pd.read_csv(source / "hospitals_provision.csv", index_col=0)
    provision.index = provision.index.astype(int)
    provision.index.name = "block_id"
    provision.to_parquet(output / "hospitals_provision.parquet", compression="zstd", index=True)

    manifest = {
        "blocks": int(len(blocks)),
        "matrix_shape": [int(matrix.shape[0]), int(matrix.shape[1])],
        "matrix_dtype": "float32",
        "demand_per_1000": 9,
        "accessibility_min": 60,
        "files": {
            path.name: path.stat().st_size for path in sorted(output.iterdir()) if path.is_file()
        },
    }
    with (output / "manifest.json").open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)

    total = sum(path.stat().st_size for path in output.iterdir() if path.is_file())
    print(f"Компактные данные: {output}")
    print(f"Всего файлов: {len(manifest['files'])}, размер: {total / 1024 / 1024:.1f} МБ")
    for name, size in manifest["files"].items():
        print(f"  {name}: {size / 1024 / 1024:.2f} МБ")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.source, args.output)


if __name__ == "__main__":
    main()

"""Запуск автоматического подбора квартала и сохранение результата в кэш."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from placement.auto import auto_search  # noqa: E402
from placement.data import load_data  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity", type=int, default=600)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    data = load_data(run_blocksnet_validation=False)

    def progress(info: dict) -> None:
        if info.get("done"):
            print(
                f"Готово: проверено {info.get('checked')} из {info.get('total')}, "
                f"ошибок {info.get('errors')}, {info.get('elapsed_sec', 0)} с"
            )
        elif info.get("checked") in {1, 100, 500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4400}:
            print(
                f"Проверено {info.get('checked')} из {info.get('total')}, "
                f"ошибок {info.get('errors')}, {info.get('elapsed_sec', 0)} с"
            )

    result = auto_search(data, args.capacity, progress_callback=progress, force=args.force)
    print(
        f"Лучшее сокращение: {result['best_reduction']} мест; "
        f"квартиры: {result['best_blocks']}; равноценных: {result['best_count']}"
    )


if __name__ == "__main__":
    main()

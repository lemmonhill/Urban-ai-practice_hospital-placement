"""Проверки расчётов, сценариев, автоподбора и API приложения."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from placement import config
from placement.auto import candidate_deficit_mcf, load_auto_cache
from placement.data import load_data
from placement.provision import (
    blocksnet_provision,
    scenario_summary,
    validate_capacity,
    validate_hospitals_payload,
)


BEST_BLOCK = 321
BEST_REDUCTION = 594
BEST_DEFICIT = 952
EXPECTED_TIES = 23


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PlacementChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_data(run_blocksnet_validation=True)
        cls.source_files = [
            config.DATA_DIR / config.MATRIX_FILE,
            config.DATA_DIR / config.BLOCKS_FILE,
            config.DATA_DIR / config.POPULATION_FILE,
            config.DATA_DIR / config.HOSPITAL_BLOCKS_FILE,
            config.DATA_DIR / config.HOSPITAL_PROVISION_FILE,
        ]
        cls.source_hashes = {path.name: file_sha256(path) for path in cls.source_files}

    def scenario(self, hospitals: dict[int, int]):
        result = blocksnet_provision(
            population=self.data.population,
            base_capacity=self.data.capacity,
            matrix=self.data.matrix,
            accessibility_min=self.data.accessibility_min,
            demand_norm=self.data.demand_norm,
            additional_capacity=hospitals,
        )
        return result, scenario_summary(result, self.data.original_result, self.data.total_demand)

    def test_00_compact_data_files(self) -> None:
        self.assertEqual(config.DATA_DIR.name, "astrakhan_compact")
        for filename in config.REQUIRED_FILES:
            self.assertTrue((config.DATA_DIR / filename).exists(), filename)
        archive = np.load(config.DATA_DIR / config.MATRIX_FILE, allow_pickle=False)
        self.assertEqual(archive["matrix"].dtype, np.float32)
        self.assertEqual(archive["matrix"].shape, (4441, 4441))
        self.assertEqual(archive["block_ids"].shape, (4441,))
        total = sum(path.stat().st_size for path in config.DATA_DIR.iterdir() if path.is_file())
        self.assertLess(total, 60 * 1024 * 1024)

    def test_01_baseline_reproduces_saved_practice(self) -> None:
        validation = self.data.validation
        self.assertTrue(validation["passed"])
        self.assertEqual(validation["blocks"], 4441)
        self.assertEqual(validation["saved_result"]["total_demand"], 6240)
        self.assertEqual(validation["saved_result"]["total_deficit"], 1546)
        self.assertAlmostEqual(validation["saved_result"]["total_provision"], 0.7522435897435897, places=12)
        self.assertTrue(validation["blocksnet_original"]["provision_strong"]["passed"])

    def test_02_capacity_is_added_to_selected_block(self) -> None:
        result, summary = self.scenario({4195: 600})
        self.assertEqual(result.at[4195, "capacity"], self.data.capacity.at[4195] + 600)
        self.assertAlmostEqual(summary["total_capacity"], self.data.total_capacity + 600)
        self.assertAlmostEqual(summary["total_capacity"] - self.data.total_capacity, 600)
        other = result.drop(index=4195)
        self.assertTrue(np.allclose(other["capacity"], self.data.capacity.drop(index=4195)))
        self.assertEqual(summary["deficit"], 1410)
        self.assertEqual(summary["deficit_reduction"], 136)

    def test_03_capacity_validation(self) -> None:
        for value in (1, 600, np.int64(300), "250", 5.0):
            self.assertEqual(validate_capacity(value), int(value))
        for value in (0, -1, 1.5, "abc", "", None, True, False, float("inf")):
            with self.assertRaises(ValueError):
                validate_capacity(value)
        self.assertEqual(
            validate_hospitals_payload(
                [
                    {"block_id": 1, "capacity": 100},
                    {"block_id": 1, "capacity": 50},
                    {"block_id": 2, "capacity": 25},
                ]
            ),
            {1: 150, 2: 25},
        )

    def test_04_scenario_does_not_modify_source_data(self) -> None:
        population = self.data.population.copy()
        capacity = self.data.capacity.copy()
        original = self.data.original_result.copy()
        self.scenario({BEST_BLOCK: 600})
        self.assertTrue(population.equals(self.data.population))
        self.assertTrue(capacity.equals(self.data.capacity))
        self.assertTrue(original.equals(self.data.original_result))
        for path in self.source_files:
            self.assertEqual(file_sha256(path), self.source_hashes[path.name], path.name)

    def test_05_auto_search_checks_every_block_independently(self) -> None:
        auto = load_auto_cache(600)
        self.assertIsNotNone(auto)
        assert auto is not None
        self.assertTrue(auto["complete"])
        self.assertEqual(auto["checked"], 4441)
        self.assertEqual(auto["total"], 4441)
        self.assertEqual(auto["errors"], 0)
        rows = auto["rows"]
        block_ids = [int(row["block_id"]) for row in rows]
        self.assertEqual(len(block_ids), 4441)
        self.assertEqual(len(set(block_ids)), 4441)
        self.assertEqual(set(block_ids), {int(value) for value in self.data.block_ids})

        demand = self.data.demand.to_numpy().astype(np.int64)
        base_capacity = self.data.capacity.to_numpy().astype(np.int64)
        matrix = self.data.matrix.to_numpy()
        position_by_block = {int(block_id): index for index, block_id in enumerate(self.data.block_ids)}
        row_by_block = {int(row["block_id"]): row for row in rows}
        for block_id in (BEST_BLOCK, 467, 4195):
            expected = candidate_deficit_mcf(
                demand=demand,
                base_capacity=base_capacity,
                matrix=matrix,
                block_position=position_by_block[block_id],
                added_capacity=600,
                accessibility_min=self.data.accessibility_min,
            )
            self.assertEqual(row_by_block[block_id]["deficit"], expected, block_id)

    def test_06_manual_placement_matches_auto_result(self) -> None:
        result, summary = self.scenario({BEST_BLOCK: 600})
        self.assertEqual(summary["deficit"], BEST_DEFICIT)
        self.assertEqual(summary["deficit_reduction"], BEST_REDUCTION)
        auto = load_auto_cache(600)
        assert auto is not None
        row = next(item for item in auto["rows"] if int(item["block_id"]) == BEST_BLOCK)
        self.assertEqual(row["deficit"], summary["deficit"])
        self.assertEqual(row["deficit_reduction"], summary["deficit_reduction"])

    def test_07_equal_best_results_are_handled(self) -> None:
        auto = load_auto_cache(600)
        assert auto is not None
        self.assertEqual(auto["best_reduction"], BEST_REDUCTION)
        self.assertEqual(auto["best_count"], EXPECTED_TIES)
        self.assertEqual(len(auto["best_blocks"]), EXPECTED_TIES)
        reductions = [row["deficit_reduction"] for row in auto["rows"]]
        self.assertLessEqual(max(reductions), BEST_REDUCTION)
        tied = {int(row["block_id"]) for row in auto["rows"] if row["deficit_reduction"] == BEST_REDUCTION}
        self.assertEqual(tied, {int(value) for value in auto["best_blocks"]})
        for block_id in (321, 467, 518):
            self.assertIn(block_id, auto["best_blocks"])
            result, summary = self.scenario({int(block_id): 600})
            self.assertEqual(summary["deficit"], BEST_DEFICIT, block_id)
            self.assertEqual(summary["deficit_reduction"], BEST_REDUCTION, block_id)

    def test_08_auto_result_has_all_required_fields(self) -> None:
        auto = load_auto_cache(600)
        assert auto is not None
        required = {
            "block_id",
            "added_capacity",
            "deficit",
            "deficit_reduction",
            "total_capacity",
            "population",
            "existing_capacity",
            "own_demand",
            "own_deficit",
        }
        for row in auto["rows"]:
            self.assertTrue(required.issubset(row.keys()))
            self.assertEqual(row["added_capacity"], 600)
            self.assertEqual(row["total_capacity"], 5330)
        reductions = [row["deficit_reduction"] for row in auto["rows"]]
        self.assertEqual(reductions, sorted(reductions, reverse=True))
        for left, right in zip(auto["rows"], auto["rows"][1:]):
            if left["deficit_reduction"] == right["deficit_reduction"]:
                self.assertLess(left["block_id"], right["block_id"])


class ApiChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app import create_app

        cls.app = create_app()
        cls.app.config.update(TESTING=True)
        cls.client = cls.app.test_client()

    def post_scenario(self, hospitals):
        return self.client.post("/api/scenario", json={"hospitals": hospitals})

    def test_01_meta_health_and_geojson(self) -> None:
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        meta = self.client.get("/api/meta").get_json()
        self.assertTrue(meta["validation"]["passed"])
        self.assertEqual(meta["blocks"], 4441)
        self.assertEqual(meta["total_demand"], 6240)
        self.assertEqual(meta["demand_linear_total"], 4194)
        self.assertEqual(meta["demand_rounding"], "ceil по каждому кварталу")
        blocks = self.client.get("/api/blocks.geojson").get_json()
        self.assertEqual(len(blocks["features"]), 4441)
        roads = self.client.get("/api/roads.geojson").get_json()
        water = self.client.get("/api/water.geojson").get_json()
        self.assertGreater(len(roads["features"]), 0)
        self.assertGreater(len(water["features"]), 0)

    def test_02_main_page_uses_local_leaflet(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("/static/vendor/leaflet/leaflet.js", html)
        self.assertIn("/static/vendor/leaflet/leaflet.css", html)
        self.assertNotIn("unpkg.com", html)
        self.assertNotIn("tile.openstreetmap.org", html)

    def test_03_scenario_response_is_consistent(self) -> None:
        response = self.post_scenario([{"block_id": BEST_BLOCK, "capacity": 600}])
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        summary = payload["summary"]
        self.assertEqual(summary["deficit"], BEST_DEFICIT)
        self.assertEqual(summary["deficit_reduction"], BEST_REDUCTION)
        self.assertEqual(summary["capacity_added"], 600)
        self.assertEqual(summary["total_capacity"], 5330)
        self.assertEqual(len(payload["blocks"]), 4441)
        self.assertAlmostEqual(sum(item["deficit"] for item in payload["blocks"]), summary["deficit"])
        self.assertAlmostEqual(sum(item["capacity"] for item in payload["blocks"]), summary["total_capacity"])
        self.assertEqual(sum(item["capacity_added"] for item in payload["blocks"]), 600)
        selected = next(item for item in payload["blocks"] if item["block_id"] == BEST_BLOCK)
        self.assertEqual(selected["capacity_added"], 600)

    def test_04_empty_scenario_and_reset(self) -> None:
        first = self.post_scenario([{"block_id": BEST_BLOCK, "capacity": 600}]).get_json()
        self.assertEqual(first["summary"]["deficit"], BEST_DEFICIT)
        reset = self.post_scenario([]).get_json()
        self.assertEqual(reset["summary"]["deficit"], 1546)
        self.assertAlmostEqual(reset["summary"]["provision_total"], 0.7522435897435897)
        self.assertEqual(reset["summary"]["capacity_added"], 0)
        self.assertEqual(sum(item["capacity_added"] for item in reset["blocks"]), 0)

    def test_05_invalid_requests(self) -> None:
        for hospitals in (
            [{"block_id": BEST_BLOCK, "capacity": 0}],
            [{"block_id": BEST_BLOCK, "capacity": -5}],
            [{"block_id": BEST_BLOCK, "capacity": 1.5}],
            [{"block_id": 99999, "capacity": 100}],
            [{"block_id": "abc", "capacity": 100}],
        ):
            response = self.post_scenario(hospitals)
            self.assertEqual(response.status_code, 400, hospitals)

    def test_06_auto_cache_endpoint(self) -> None:
        response = self.client.get("/api/auto/cache/600")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["checked"], 4441)
        self.assertEqual(payload["best_reduction"], BEST_REDUCTION)
        self.assertEqual(payload["best_count"], EXPECTED_TIES)
        self.assertEqual(self.client.get("/api/auto/cache/7").status_code, 404)

    def test_07_saved_scenarios_crud(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved_scenarios.json"
            with patch("placement.scenarios.SCENARIOS_PATH", path):
                self.assertEqual(self.client.get("/api/scenarios").get_json(), [])
                created = self.client.post(
                    "/api/scenarios",
                    json={
                        "name": "Тестовый сценарий",
                        "hospitals": [{"block_id": BEST_BLOCK, "capacity": 600}],
                        "summary": {"deficit": BEST_DEFICIT, "deficit_reduction": BEST_REDUCTION},
                    },
                )
                self.assertEqual(created.status_code, 201)
                record = created.get_json()
                self.assertEqual(record["name"], "Тестовый сценарий")
                self.assertEqual(record["hospitals"], [{"block_id": BEST_BLOCK, "capacity": 600}])
                listed = self.client.get("/api/scenarios").get_json()
                self.assertEqual(len(listed), 1)
                self.assertEqual(listed[0]["id"], record["id"])
                deleted = self.client.delete(f"/api/scenarios/{record['id']}")
                self.assertEqual(deleted.status_code, 200)
                self.assertEqual(self.client.get("/api/scenarios").get_json(), [])
                self.assertEqual(self.client.delete(f"/api/scenarios/{record['id']}").status_code, 404)

                bad = self.client.post("/api/scenarios", json={"name": "", "hospitals": []})
                self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()

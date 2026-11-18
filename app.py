"""Flask-приложение для оценки размещения новых больниц (Астрахань)."""

from __future__ import annotations

import json
import os
from typing import Any

from flask import Flask, Response, jsonify, render_template, request

from placement import config
from placement.auto import AutoJobManager, load_auto_cache
from placement.data import load_data, metadata
from placement.provision import (
    ProvisionError,
    blocksnet_provision,
    scenario_summary,
    validate_capacity,
    validate_hospitals_payload,
)
from placement.scenarios import delete_scenario, list_scenarios, save_scenario


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["JSON_AS_ASCII"] = False

    validate_blocksnet = os.environ.get("HOSPITAL_PLACEMENT_VALIDATE_BLOCKSNET", "1") != "0"
    print("Загрузка сохранённых данных и проверка исходного расчёта...")
    data = load_data(run_blocksnet_validation=validate_blocksnet)
    print(
        "Данные загружены: кварталов %d, население %.0f, дефицит %.0f мест"
        % (len(data.blocks), data.population.sum(), data.baseline_deficit)
    )
    jobs = AutoJobManager()

    validation_path = config.RESULTS_DIR / "validation.json"
    with validation_path.open("w", encoding="utf-8") as file:
        json.dump(data.validation, file, ensure_ascii=False, indent=2)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"ok": True, "blocks": len(data.blocks)})

    @app.get("/api/meta")
    def meta() -> Any:
        return jsonify(metadata(data))

    @app.get("/api/validation")
    def validation() -> Any:
        return jsonify(data.validation)

    @app.get("/api/blocks.geojson")
    def blocks_geojson() -> Any:
        return Response(
            json.dumps(data.blocks_geojson, ensure_ascii=False),
            mimetype="application/geo+json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/api/hospitals.geojson")
    def hospitals_geojson() -> Any:
        if data.hospitals is None:
            return jsonify({"type": "FeatureCollection", "features": []})
        frame = data.hospitals[["capacity", "geometry"]].copy() if "capacity" in data.hospitals.columns else data.hospitals[
            ["geometry"]
        ].copy()
        for column in frame.columns:
            if column != "geometry":
                frame[column] = frame[column].where(frame[column].notna(), None)
        return Response(
            json.dumps(json.loads(frame.to_json(drop_id=True)), ensure_ascii=False),
            mimetype="application/geo+json",
        )

    @app.get("/api/boundaries.geojson")
    def boundaries_geojson() -> Any:
        if data.boundaries is None:
            return jsonify({"type": "FeatureCollection", "features": []})
        return Response(
            json.dumps(json.loads(data.boundaries.to_json(drop_id=True)), ensure_ascii=False),
            mimetype="application/geo+json",
        )

    @app.get("/api/roads.geojson")
    def roads_geojson() -> Any:
        return Response(
            json.dumps(data.roads_geojson, ensure_ascii=False),
            mimetype="application/geo+json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/api/water.geojson")
    def water_geojson() -> Any:
        return Response(
            json.dumps(data.water_geojson, ensure_ascii=False),
            mimetype="application/geo+json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.post("/api/scenario")
    def scenario() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            hospitals = validate_hospitals_payload(payload.get("hospitals", []))
            result = blocksnet_provision(
                population=data.population,
                base_capacity=data.capacity,
                matrix=data.matrix,
                accessibility_min=data.accessibility_min,
                demand_norm=data.demand_norm,
                additional_capacity=hospitals,
            )
        except (ValueError, KeyError, ProvisionError) as error:
            return jsonify({"error": str(error)}), 400

        summary = scenario_summary(result, data.original_result, data.total_demand)
        summary["capacity_added"] = int(sum(hospitals.values()))
        summary["capacity_added_by_block"] = {str(k): int(v) for k, v in hospitals.items()}

        original = data.original_result
        records = []
        for block_id in data.block_ids:
            block_id = int(block_id)
            demand = int(result.at[block_id, "demand"])
            provision = result.at[block_id, "provision_strong"]
            provision_original = original.at[block_id, "provision_strong"]
            records.append(
                {
                    "block_id": block_id,
                    "population": float(data.population.at[block_id]),
                    "demand": demand,
                    "capacity_original": float(data.capacity.at[block_id]),
                    "capacity_added": int(hospitals.get(block_id, 0)),
                    "capacity": float(result.at[block_id, "capacity"]),
                    "demand_within": float(result.at[block_id, "demand_within"]),
                    "provision_original": None if provision_original != provision_original else float(provision_original),
                    "provision": None if provision != provision else float(provision),
                    "deficit_original": float(original.at[block_id, "deficit"]),
                    "deficit": float(result.at[block_id, "deficit"]),
                }
            )

        return jsonify(
            {
                "implementation": "blocksnet_competitive_provision",
                "implementation_label": "BlocksNet competitive_provision (точный расчёт по всей сети)",
                "hospitals": [{"block_id": k, "capacity": v} for k, v in sorted(hospitals.items())],
                "summary": summary,
                "blocks": records,
            }
        )

    @app.post("/api/auto/start")
    def auto_start() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            capacity = validate_capacity(payload.get("capacity", config.DEFAULT_AUTO_CAPACITY))
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        force = bool(payload.get("force", False))
        job_id = jobs.start(data, capacity, force=force)
        return jsonify({"job_id": job_id, "capacity": capacity})

    @app.get("/api/auto/status/<job_id>")
    def auto_status(job_id: str) -> Any:
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "Задание не найдено"}), 404
        return jsonify(job)

    @app.get("/api/auto/cache/<int:capacity>")
    def auto_cache(capacity: int) -> Any:
        result = load_auto_cache(capacity)
        if result is None:
            return jsonify({"error": "Для этой вместимости результат ещё не рассчитан"}), 404
        return jsonify(result)

    @app.get("/api/scenarios")
    def scenarios_list() -> Any:
        return jsonify(list_scenarios())

    @app.post("/api/scenarios")
    def scenarios_save() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            record = save_scenario(
                payload.get("name", ""),
                payload.get("hospitals", []),
                payload.get("summary", {}),
            )
        except (ValueError, KeyError, TypeError) as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(record), 201

    @app.delete("/api/scenarios/<scenario_id>")
    def scenarios_delete(scenario_id: str) -> Any:
        if not delete_scenario(scenario_id):
            return jsonify({"error": "Сценарий не найден"}), 404
        return jsonify({"ok": True})

    return app


app = create_app()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Приложение оценки размещения больниц")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8050")))
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)

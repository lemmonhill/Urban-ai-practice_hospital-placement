const state = {
  meta: null,
  blocksGeo: null,
  mode: "original",
  selectedBlock: null,
  blockLayers: new Map(),
  blockMap: new Map(),
  scenarioHospitals: {},
  scenarioResult: null,
  scenarioBlockMap: new Map(),
  autoResult: null,
  autoJobId: null,
  autoTimer: null,
  blockSort: { key: "deficit", dir: "desc" },
  blockFilter: "",
  blockPage: 1,
  blockPageSize: 50,
  autoFilter: "",
  autoPage: 1,
  autoPageSize: 100,
  savedScenarios: [],
  compareSelected: new Set(),
  map: null,
  blocksLayer: null,
  newHospitalsLayer: null,
  selectedLayer: null,
};

const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const decimalFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const twoDecimalFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

function fmtInt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return numberFormat.format(Number(value));
}

function fmtDec(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function fmtDeltaPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const pct = Number(value) * 100;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(1)} п.п.`;
}

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }
  if (!response.ok) {
    const message = payload && payload.error ? payload.error : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

let toastTimer = null;
function toast(message, isError = false) {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.classList.toggle("error", Boolean(isError));
  element.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    element.hidden = true;
  }, isError ? 7000 : 4000);
}

function provisionColor(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "#cbd5e1";
  const v = Number(value);
  if (v < 0.25) return "#b91c1c";
  if (v < 0.5) return "#ef4444";
  if (v < 0.75) return "#f59e0b";
  if (v < 1) return "#eab308";
  return "#15803d";
}

function deltaColor(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "#cbd5e1";
  const v = Number(value);
  if (v > 0.02) return "#15803d";
  if (v > 0.000001) return "#86efac";
  if (v < -0.02) return "#b91c1c";
  if (v < -0.000001) return "#fca5a5";
  return "#e2e8f0";
}

function currentBlockData(blockId) {
  const id = Number(blockId);
  if (state.scenarioResult) {
    const item = state.scenarioBlockMap.get(id);
    if (item) return item;
  }
  const feature = state.blockMap.get(id);
  if (!feature) return null;
  const properties = feature.properties;
  return {
    block_id: id,
    population: properties.population,
    demand: properties.demand,
    capacity_original: properties.capacity,
    capacity_added: 0,
    capacity: properties.capacity,
    provision_original: properties.provision_original,
    provision: properties.provision_original,
    deficit_original: properties.deficit_original,
    deficit: properties.deficit_original,
  };
}

function styleFeature(feature) {
  const id = Number(feature.properties.block_id);
  const original = feature.properties.provision_original;
  const scenario = state.scenarioBlockMap.get(id);
  let fillColor = "#cbd5e1";
  if (state.mode === "original") {
    fillColor = provisionColor(original);
  } else if (state.mode === "scenario") {
    fillColor = provisionColor(scenario ? scenario.provision : original);
  } else if (state.mode === "change") {
    const change = scenario ? Number(scenario.provision) - Number(scenario.provision_original) : 0;
    fillColor = scenario ? deltaColor(change) : "#cbd5e1";
  }
  const selected = state.selectedBlock === id;
  return {
    color: selected ? "#0f172a" : "#f8fafc",
    weight: selected ? 3 : 0.6,
    opacity: 1,
    fillColor,
    fillOpacity: state.mode === "change" ? 0.8 : 0.62,
  };
}

function refreshBlockStyles() {
  if (!state.blocksLayer) return;
  state.blocksLayer.eachLayer((layer) => layer.setStyle(styleFeature(layer.feature)));
}

function popupHtml(blockId) {
  const feature = state.blockMap.get(Number(blockId));
  if (!feature) return "";
  const data = currentBlockData(blockId);
  return `
    <b>Квартал ${blockId}</b><br>
    Население: ${fmtInt(data.population)} чел.<br>
    Потребность: ${fmtInt(data.demand)} мест<br>
    Вместимость: ${fmtInt(data.capacity)} мест<br>
    Обеспеченность: ${fmtPct(data.provision)}<br>
    Дефицит: ${fmtInt(data.deficit)} мест
  `;
}

function selectBlock(blockId, openPopup = false) {
  const id = Number(blockId);
  state.selectedBlock = id;
  const manual = document.getElementById("manualBlock");
  manual.value = String(id);
  refreshBlockStyles();
  renderSelectedCard();
  const layer = state.blockLayers.get(id);
  if (layer) {
    layer.bindPopup(popupHtml(id));
    if (openPopup) layer.openPopup();
  }
}

function setupMap(blocksGeo, hospitalsGeo, boundariesGeo, roadsGeo, waterGeo) {
  const map = L.map("map", { preferCanvas: true, zoomControl: true }).setView([46.36, 48.04], 11);
  state.map = map;

  if (waterGeo && waterGeo.features && waterGeo.features.length) {
    L.geoJSON(waterGeo, {
      style: { color: "#93c5fd", weight: 0.8, fillColor: "#bfdbfe", fillOpacity: 0.75 },
      interactive: false,
    }).addTo(map);
  }

  if (roadsGeo && roadsGeo.features && roadsGeo.features.length) {
    L.geoJSON(roadsGeo, {
      style: { color: "#cbd5e1", weight: 1.2, opacity: 0.9 },
      interactive: false,
    }).addTo(map);
  }

  if (boundariesGeo && boundariesGeo.features && boundariesGeo.features.length) {
    L.geoJSON(boundariesGeo, {
      style: { color: "#334155", weight: 2, fill: false, opacity: 0.9 },
      interactive: false,
    }).addTo(map);
  }

  state.blocksGeo = blocksGeo;
  for (const feature of blocksGeo.features) {
    state.blockMap.set(Number(feature.properties.block_id), feature);
  }

  state.blocksLayer = L.geoJSON(blocksGeo, {
    style: styleFeature,
    onEachFeature: (feature, layer) => {
      const id = Number(feature.properties.block_id);
      state.blockLayers.set(id, layer);
      layer.bindTooltip(
        `<div class="block-tooltip"><b>Квартал ${id}</b><br>Население: ${fmtInt(
          feature.properties.population,
        )}<br>Обеспеченность: ${fmtPct(feature.properties.provision_original)}<br>Дефицит: ${fmtInt(
          feature.properties.deficit_original,
        )} мест</div>`,
        { sticky: true },
      );
      layer.on("click", () => selectBlock(id, false));
    },
  }).addTo(map);

  state.newHospitalsLayer = L.layerGroup().addTo(map);

  if (hospitalsGeo && hospitalsGeo.features && hospitalsGeo.features.length) {
    L.geoJSON(hospitalsGeo, {
      style: {
        color: "#7f1d1d",
        weight: 1,
        fillColor: "#ef4444",
        fillOpacity: 0.75,
      },
      onEachFeature: (feature, layer) => {
        const capacity = feature.properties.capacity;
        layer.bindPopup(`<b>Существующая больница</b><br>Вместимость: ${fmtInt(capacity)} мест`);
      },
    }).addTo(map);
  }

  const bounds = state.blocksLayer.getBounds();
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [10, 10] });
}

function renderNewHospitalMarkers() {
  if (!state.newHospitalsLayer) return;
  state.newHospitalsLayer.clearLayers();
  for (const [blockIdText, capacity] of Object.entries(state.scenarioHospitals)) {
    const id = Number(blockIdText);
    const layer = state.blockLayers.get(id);
    if (!layer) continue;
    const center = layer.getBounds().getCenter();
    L.circleMarker(center, {
      radius: 10,
      color: "#1e3a8a",
      weight: 2,
      fillColor: "#3b82f6",
      fillOpacity: 0.95,
    })
      .bindTooltip(`Новая больница: ${fmtInt(capacity)} мест`, { permanent: false })
      .bindPopup(`<b>Новая больница</b><br>Квартал ${id}<br>Вместимость: ${fmtInt(capacity)} мест`)
      .addTo(state.newHospitalsLayer);
  }
}

function updateLegend() {
  const legend = document.getElementById("legend");
  if (state.mode === "change") {
    legend.innerHTML = `
      <div class="legend-row"><span class="legend-swatch" style="background:#b91c1c"></span>обеспеченность снизилась</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#fca5a5"></span>небольшое снижение</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#e2e8f0"></span>без изменений</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#86efac"></span>небольшой рост</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#15803d"></span>обеспеченность выросла</div>
      <div class="legend-note">Разница доли удовлетворённой потребности относительно исходного состояния, п.п.</div>
    `;
    return;
  }
  legend.innerHTML = `
    <div class="legend-row"><span class="legend-swatch" style="background:#b91c1c"></span>0–25%</div>
    <div class="legend-row"><span class="legend-swatch" style="background:#ef4444"></span>25–50%</div>
    <div class="legend-row"><span class="legend-swatch" style="background:#f59e0b"></span>50–75%</div>
    <div class="legend-row"><span class="legend-swatch" style="background:#eab308"></span>75–100%</div>
    <div class="legend-row"><span class="legend-swatch" style="background:#15803d"></span>100%</div>
    <div class="legend-row"><span class="legend-swatch" style="background:#cbd5e1"></span>нет данных / нулевой спрос</div>
    <div class="legend-note">Обеспеченность — доля расчётной потребности, покрытая вместимостью больниц в пределах 60 минут пешком.</div>
  `;
}

function setMode(mode) {
  if (mode !== "original" && !state.scenarioResult) {
    toast("Сначала рассчитайте сценарий, чтобы увидеть его на карте.", true);
    return;
  }
  state.mode = mode;
  document.querySelectorAll("#mapMode button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  updateLegend();
  refreshBlockStyles();
}

function renderKpis() {
  const container = document.getElementById("kpis");
  const summary = state.scenarioResult
    ? state.scenarioResult.summary
    : {
        total_capacity: state.meta.total_capacity,
        capacity_added: 0,
        deficit: state.meta.baseline_deficit,
        baseline_deficit: state.meta.baseline_deficit,
        deficit_reduction: 0,
        deficit_reduction_share: 0,
        provision_total: state.meta.baseline_provision,
        baseline_provision_total: state.meta.baseline_provision,
      };

  const cards = [
    {
      label: "Население территории",
      value: fmtInt(state.meta.population_total),
      sub: "расчётная калиброванная оценка, чел.",
      cls: "blue",
    },
    {
      label: "Расчётная потребность в местах",
      value: fmtInt(state.meta.total_demand),
      sub: `${state.meta.demand_per_1000} мест на 1000 жителей`,
    },
    {
      label: "Вместимость сети больниц",
      value: fmtInt(summary.total_capacity),
      sub: summary.capacity_added > 0 ? `добавлено ${fmtInt(summary.capacity_added)} мест` : "исходная",
    },
    {
      label: "Остаточный дефицит мест",
      value: fmtInt(summary.deficit),
      sub: `исходно ${fmtInt(summary.baseline_deficit)} мест`,
      cls: summary.deficit > 0 ? "warn" : "good",
    },
    {
      label: "Сокращение дефицита",
      value: fmtInt(summary.deficit_reduction),
      sub: `${(summary.deficit_reduction_share * 100).toFixed(1)}% от исходного дефицита`,
      cls: summary.deficit_reduction > 0 ? "good" : "",
    },
    {
      label: "Обеспеченность населения, %",
      value: fmtPct(summary.provision_total),
      sub: `исходно ${fmtPct(summary.baseline_provision_total)}`,
      cls: summary.provision_total > summary.baseline_provision_total ? "good" : "",
    },
  ];

  container.innerHTML = cards
    .map(
      (card) => `
      <div class="kpi ${card.cls || ""}">
        <div class="label">${esc(card.label)}</div>
        <div class="value">${card.value}</div>
        <div class="sub">${esc(card.sub || "")}</div>
      </div>
    `,
    )
    .join("");
}

function renderSelectedCard() {
  const card = document.getElementById("selectedCard");
  if (state.selectedBlock === null) {
    card.className = "selected-card muted";
    card.textContent = "Нажмите на квартал на карте";
    return;
  }
  const data = currentBlockData(state.selectedBlock);
  if (!data) {
    card.className = "selected-card muted";
    card.textContent = "Квартал не найден";
    return;
  }
  const feature = state.blockMap.get(Number(state.selectedBlock));
  const hospitalCount = feature ? feature.properties.hospital_count : 0;
  const deltaProvision =
    data.provision !== null && data.provision_original !== null
      ? Number(data.provision) - Number(data.provision_original)
      : null;
  const deltaDeficit = Number(data.deficit_original) - Number(data.deficit);
  card.className = "selected-card";
  card.innerHTML = `
    <div class="block-title">
      <strong>Квартал ${data.block_id}</strong>
      <span>больниц: ${fmtInt(hospitalCount)}</span>
    </div>
    <dl class="kv">
      <dt>Население</dt><dd>${fmtInt(data.population)} чел.</dd>
      <dt>Потребность</dt><dd>${fmtInt(data.demand)} мест</dd>
      <dt>Вместимость исходная</dt><dd>${fmtInt(data.capacity_original)} мест</dd>
      <dt>Новые больницы</dt><dd>${fmtInt(data.capacity_added)} мест</dd>
      <dt>Вместимость в сценарии</dt><dd>${fmtInt(data.capacity)} мест</dd>
      <dt>Обеспеченность исходная</dt><dd>${fmtPct(data.provision_original)}</dd>
      <dt>Обеспеченность в сценарии</dt><dd class="${deltaProvision > 0 ? "positive" : ""}">${fmtPct(data.provision)}</dd>
      <dt>Дефицит исходный</dt><dd>${fmtInt(data.deficit_original)} мест</dd>
      <dt>Дефицит в сценарии</dt><dd class="${deltaDeficit > 0 ? "positive" : ""}">${fmtInt(data.deficit)} мест</dd>
      <dt>Изменение обеспеченности</dt><dd class="${deltaProvision > 0 ? "positive" : deltaProvision < 0 ? "negative" : ""}">${fmtDeltaPct(deltaProvision)}</dd>
    </dl>
  `;
}

function renderPlannedHospitals() {
  const container = document.getElementById("plannedHospitals");
  const entries = Object.entries(state.scenarioHospitals);
  if (!entries.length) {
    container.className = "planned-list muted";
    container.textContent = "Новые больницы не заданы";
    return;
  }
  container.className = "planned-list";
  container.innerHTML = entries
    .map(
      ([blockId, capacity]) => `
      <div class="planned-item">
        <span>Квартал ${blockId}: <b>${fmtInt(capacity)}</b> мест</span>
        <button type="button" data-remove-block="${blockId}" title="Удалить">×</button>
      </div>
    `,
    )
    .join("");
  container.querySelectorAll("[data-remove-block]").forEach((button) => {
    button.addEventListener("click", () => {
      delete state.scenarioHospitals[button.dataset.removeBlock];
      renderPlannedHospitals();
      renderNewHospitalMarkers();
    });
  });
}

function mergeHospital(blockId, capacity) {
  const id = Number(blockId);
  state.scenarioHospitals[id] = Number(state.scenarioHospitals[id] || 0) + Number(capacity);
}

async function calculateScenario() {
  const status = document.getElementById("scenarioStatus");
  const hospitals = Object.entries(state.scenarioHospitals).map(([blockId, capacity]) => ({
    block_id: Number(blockId),
    capacity: Number(capacity),
  }));
  status.className = "status";
  status.textContent = "Пересчёт обеспеченности BlocksNet competitive_provision…";
  try {
    const payload = await fetchJson("/api/scenario", {
      method: "POST",
      body: JSON.stringify({ hospitals }),
    });
    state.scenarioResult = payload;
    state.scenarioBlockMap = new Map(payload.blocks.map((item) => [Number(item.block_id), item]));
    state.blockPage = 1;
    state.mode = "scenario";
    document.querySelectorAll("#mapMode button").forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === "scenario");
    });
    updateLegend();
    refreshBlockStyles();
    renderKpis();
    renderSelectedCard();
    renderBlockTable();
    renderNewHospitalMarkers();
    status.className = "status ok";
    status.textContent =
      hospitals.length === 0
        ? "Показано исходное состояние: новые больницы не заданы."
        : `Готово. Сокращение дефицита: ${fmtInt(payload.summary.deficit_reduction)} мест.`;
  } catch (error) {
    status.className = "status error";
    status.textContent = `Ошибка: ${error.message}`;
    toast(`Не удалось рассчитать сценарий: ${error.message}`, true);
  }
}

function resetScenario() {
  state.scenarioHospitals = {};
  state.scenarioResult = null;
  state.scenarioBlockMap = new Map();
  state.mode = "original";
  state.blockPage = 1;
  state.blockSort = { key: "deficit", dir: "desc" };
  document.querySelectorAll("#mapMode button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === "original");
  });
  const status = document.getElementById("scenarioStatus");
  status.className = "status";
  status.textContent = "Сценарий сброшен.";
  renderPlannedHospitals();
  renderNewHospitalMarkers();
  updateLegend();
  refreshBlockStyles();
  renderKpis();
  renderSelectedCard();
  renderBlockTable();
}

function blockRows() {
  if (state.scenarioResult) return state.scenarioResult.blocks;
  return Array.from(state.blockMap.values()).map((feature) => ({
    block_id: Number(feature.properties.block_id),
    population: feature.properties.population,
    demand: feature.properties.demand,
    capacity_original: feature.properties.capacity,
    capacity_added: 0,
    capacity: feature.properties.capacity,
    provision_original: feature.properties.provision_original,
    provision: feature.properties.provision_original,
    deficit_original: feature.properties.deficit_original,
    deficit: feature.properties.deficit_original,
  }));
}

function blockValue(row, key) {
  if (key === "provision_change") {
    return Number(row.provision) - Number(row.provision_original);
  }
  if (key === "deficit_change") {
    return Number(row.deficit) - Number(row.deficit_original);
  }
  return row[key];
}

const blockColumns = [
  { key: "block_id", label: "Квартал" },
  { key: "population", label: "Население, чел." },
  { key: "demand", label: "Потребность, мест" },
  { key: "capacity_original", label: "Вместимость исходная, мест" },
  { key: "capacity_added", label: "Новые больницы, мест" },
  { key: "capacity", label: "Вместимость в сценарии, мест" },
  { key: "provision_original", label: "Обеспеченность исходная, %" },
  { key: "provision", label: "Обеспеченность в сценарии, %" },
  { key: "deficit_original", label: "Дефицит исходный, мест" },
  { key: "deficit", label: "Дефицит в сценарии, мест" },
  { key: "provision_change", label: "Δ обеспеченности, п.п." },
  { key: "deficit_change", label: "Δ дефицита, мест" },
];

function renderBlockTable() {
  const table = document.getElementById("blocksTable");
  const filter = state.blockFilter.trim();
  let rows = blockRows();
  if (filter) rows = rows.filter((row) => String(row.block_id).includes(filter));

  const { key, dir } = state.blockSort;
  rows = rows.slice().sort((a, b) => {
    const av = blockValue(a, key);
    const bv = blockValue(b, key);
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string" || typeof bv === "string") {
      return dir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    }
    return dir === "asc" ? Number(av) - Number(bv) : Number(bv) - Number(av);
  });

  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / state.blockPageSize));
  state.blockPage = Math.min(Math.max(1, state.blockPage), totalPages);
  const start = (state.blockPage - 1) * state.blockPageSize;
  const pageRows = rows.slice(start, start + state.blockPageSize);

  table.querySelector("thead").innerHTML = `<tr>${blockColumns
    .map(
      (column) =>
        `<th data-sort="${column.key}" class="${key === column.key ? "sorted" : ""}">${esc(column.label)}${
          key === column.key ? (dir === "asc" ? " ▲" : " ▼") : ""
        }</th>`,
    )
    .join("")}</tr>`;

  table.querySelector("tbody").innerHTML = pageRows
    .map((row) => {
      const provisionChange = Number(row.provision) - Number(row.provision_original);
      const deficitChange = Number(row.deficit) - Number(row.deficit_original);
      const cells = [
        row.block_id,
        fmtInt(row.population),
        fmtInt(row.demand),
        fmtInt(row.capacity_original),
        fmtInt(row.capacity_added),
        fmtInt(row.capacity),
        fmtPct(row.provision_original),
        fmtPct(row.provision),
        fmtInt(row.deficit_original),
        fmtInt(row.deficit),
        fmtDeltaPct(provisionChange),
        `${deficitChange < 0 ? "−" : deficitChange > 0 ? "+" : ""}${fmtInt(Math.abs(deficitChange))}`,
      ];
      const classes = [
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        provisionChange > 0 ? "positive" : provisionChange < 0 ? "negative" : "",
        "",
        "",
        provisionChange > 0 ? "positive" : provisionChange < 0 ? "negative" : "",
        deficitChange < 0 ? "positive" : deficitChange > 0 ? "negative" : "",
      ];
      return `<tr data-block="${row.block_id}" class="${state.selectedBlock === Number(row.block_id) ? "selected" : ""}">${cells
        .map((cell, index) => `<td class="${classes[index] || ""}">${cell}</td>`)
        .join("")}</tr>`;
    })
    .join("");

  table.querySelectorAll("th[data-sort]").forEach((header) => {
    header.addEventListener("click", () => {
      const sortKey = header.dataset.sort;
      if (state.blockSort.key === sortKey) {
        state.blockSort.dir = state.blockSort.dir === "asc" ? "desc" : "asc";
      } else {
        state.blockSort = { key: sortKey, dir: "desc" };
      }
      state.blockPage = 1;
      renderBlockTable();
    });
  });
  table.querySelectorAll("tbody tr").forEach((row) => {
    row.addEventListener("click", () => selectBlock(Number(row.dataset.block), true));
  });

  document.getElementById("blockTableInfo").textContent = total
    ? `Показано ${start + 1}–${Math.min(start + state.blockPageSize, total)} из ${total} кварталов`
    : "Ничего не найдено";

  const pager = document.getElementById("blockPager");
  pager.innerHTML = `
    <button type="button" data-page="1" ${state.blockPage === 1 ? "disabled" : ""}>В начало</button>
    <button type="button" data-page="${state.blockPage - 1}" ${state.blockPage === 1 ? "disabled" : ""}>Назад</button>
    <span>Страница ${state.blockPage} из ${totalPages}</span>
    <button type="button" data-page="${state.blockPage + 1}" ${state.blockPage === totalPages ? "disabled" : ""}>Вперёд</button>
    <button type="button" data-page="${totalPages}" ${state.blockPage === totalPages ? "disabled" : ""}>В конец</button>
  `;
  pager.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      state.blockPage = Number(button.dataset.page);
      renderBlockTable();
    });
  });
}

function renderAutoTable() {
  const table = document.getElementById("autoTable");
  const info = document.getElementById("autoTableInfo");
  const exportButton = document.getElementById("exportAuto");
  if (!state.autoResult) {
    table.querySelector("thead").innerHTML = "";
    table.querySelector("tbody").innerHTML = "";
    document.getElementById("autoPager").innerHTML = "";
    info.textContent = "Автоподбор ещё не запускался";
    exportButton.disabled = true;
    return;
  }
  const result = state.autoResult;
  const filter = state.autoFilter.trim();
  const rows = (filter
    ? result.rows.filter((row) => String(row.block_id).includes(filter))
    : result.rows
  )
    .slice()
    .sort(
      (a, b) =>
        Number(b.deficit_reduction) - Number(a.deficit_reduction) ||
        Number(a.block_id) - Number(b.block_id),
    );
  const totalPages = Math.max(1, Math.ceil(rows.length / state.autoPageSize));
  state.autoPage = Math.min(Math.max(1, state.autoPage), totalPages);
  const start = (state.autoPage - 1) * state.autoPageSize;
  const pageRows = rows.slice(start, start + state.autoPageSize);

  table.querySelector("thead").innerHTML = `
    <tr>
      <th>Ранг</th>
      <th>Квартал</th>
      <th>Сокращение дефицита, мест</th>
      <th>Дефицит после, мест</th>
      <th>Обеспеченность после, %</th>
      <th>Население, чел.</th>
      <th>Собственный дефицит, мест</th>
      <th>Существующая вместимость, мест</th>
      <th>Действие</th>
    </tr>
  `;
  table.querySelector("tbody").innerHTML = pageRows
    .map((row, index) => {
      const rank = start + index + 1;
      const provision = 1 - Number(row.deficit) / state.meta.total_demand;
      const best = result.best_blocks.includes(Number(row.block_id));
      return `
        <tr data-block="${row.block_id}" class="${best ? "best" : ""} ${
          state.selectedBlock === Number(row.block_id) ? "selected" : ""
        }">
          <td>${rank}</td>
          <td>${row.block_id}${best ? " ★" : ""}</td>
          <td class="positive">${fmtInt(row.deficit_reduction)}</td>
          <td>${fmtInt(row.deficit)}</td>
          <td>${fmtPct(provision)}</td>
          <td>${fmtInt(row.population)}</td>
          <td>${fmtInt(row.own_deficit)}</td>
          <td>${fmtInt(row.existing_capacity)}</td>
          <td><button type="button" class="btn btn-secondary" data-apply="${row.block_id}">Показать</button></td>
        </tr>
      `;
    })
    .join("");

  table.querySelectorAll("[data-apply]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      applyAutoCandidate(Number(button.dataset.apply));
    });
  });
  table.querySelectorAll("tbody tr").forEach((row) => {
    row.addEventListener("click", () => applyAutoCandidate(Number(row.dataset.block)));
  });

  info.textContent = result.complete
    ? `Проверено ${fmtInt(result.checked)} из ${fmtInt(result.total)} кварталов. Лучшее сокращение: ${fmtInt(
        result.best_reduction,
      )} мест.`
    : `Перебор неполный: успешно проверено ${fmtInt(result.checked)} из ${fmtInt(result.total)}, ошибок ${fmtInt(
        result.errors,
      )}. Лучший результат указан только среди успешно проверенных вариантов.`;
  exportButton.disabled = false;

  const pager = document.getElementById("autoPager");
  pager.innerHTML = `
    <button type="button" data-page="1" ${state.autoPage === 1 ? "disabled" : ""}>В начало</button>
    <button type="button" data-page="${state.autoPage - 1}" ${state.autoPage === 1 ? "disabled" : ""}>Назад</button>
    <span>Страница ${state.autoPage} из ${totalPages}</span>
    <button type="button" data-page="${state.autoPage + 1}" ${
      state.autoPage === totalPages ? "disabled" : ""
    }>Вперёд</button>
    <button type="button" data-page="${totalPages}" ${
      state.autoPage === totalPages ? "disabled" : ""
    }>В конец</button>
  `;
  pager.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      state.autoPage = Number(button.dataset.page);
      renderAutoTable();
    });
  });
}

function renderAutoBest() {
  const container = document.getElementById("autoBest");
  if (!state.autoResult) {
    container.hidden = true;
    return;
  }
  const result = state.autoResult;
  container.hidden = false;
  const blocks = result.best_blocks.join(", ");
  const tieText =
    result.best_count > 1
      ? `Найдено равноценных лучших кварталов: ${result.best_count} (${blocks}).`
      : `Лучший квартал: ${blocks}.`;
  const incompleteText = result.complete
    ? ""
    : `<div class="status error">Перебор неполный: ошибок ${fmtInt(result.errors)}. Нельзя утверждать, что лучший вариант найден среди всех кварталов.</div>`;
  container.innerHTML = `
    <strong>${tieText}</strong>
    <div>Сокращение суммарного дефицита: ${fmtInt(result.best_reduction)} мест. Дефицит после размещения: ${fmtInt(
      result.baseline_deficit - result.best_reduction,
    )} мест.</div>
    <div>Вместимость новой больницы: ${fmtInt(result.capacity)} мест. Проверено кварталов: ${fmtInt(
      result.checked,
    )} из ${fmtInt(result.total)}.</div>
    <div class="legend-note">${esc(result.verification)}</div>
    ${incompleteText}
  `;
}

function applyAutoCandidate(blockId) {
  if (!state.autoResult) return;
  const capacity = state.autoResult.capacity;
  state.scenarioHospitals = { [Number(blockId)]: Number(capacity) };
  renderPlannedHospitals();
  renderNewHospitalMarkers();
  selectBlock(blockId, true);
  toast(`Сценарий: одна больница на ${fmtInt(capacity)} мест в квартале ${blockId}. Идёт пересчёт.`);
  calculateScenario();
}

async function startAutoSearch() {
  const input = document.getElementById("autoCapacity");
  const capacity = Number(input.value);
  if (!Number.isInteger(capacity) || capacity <= 0) {
    toast("Вместимость новой больницы должна быть положительным целым числом.", true);
    return;
  }
  const progressWrap = document.getElementById("autoProgressWrap");
  const progressBar = document.getElementById("autoProgressBar");
  const progressText = document.getElementById("autoProgressText");
  const status = document.getElementById("autoStatus");
  progressWrap.hidden = false;
  progressBar.style.width = "0%";
  progressText.textContent = "Запуск перебора кварталов…";
  status.className = "status";
  status.textContent = "";
  document.getElementById("autoSearch").disabled = true;
  try {
    const payload = await fetchJson("/api/auto/start", {
      method: "POST",
      body: JSON.stringify({ capacity }),
    });
    state.autoJobId = payload.job_id;
    if (state.autoTimer) clearInterval(state.autoTimer);
    state.autoTimer = setInterval(() => pollAutoJob(), 700);
  } catch (error) {
    status.className = "status error";
    status.textContent = `Ошибка: ${error.message}`;
    document.getElementById("autoSearch").disabled = false;
  }
}

async function pollAutoJob() {
  if (!state.autoJobId) return;
  const status = document.getElementById("autoStatus");
  const progressBar = document.getElementById("autoProgressBar");
  const progressText = document.getElementById("autoProgressText");
  try {
    const job = await fetchJson(`/api/auto/status/${state.autoJobId}`);
    const percent = job.total ? Math.min(100, (Number(job.checked) / Number(job.total)) * 100) : 0;
    progressBar.style.width = `${percent}%`;
    progressText.textContent = `Проверено ${fmtInt(job.checked)} из ${fmtInt(job.total)} кварталов${
      job.errors ? `, ошибок ${fmtInt(job.errors)}` : ""
    }${job.elapsed_sec ? `, ${fmtDec(job.elapsed_sec, 0)} с` : ""}`;

    if (job.status === "done") {
      clearInterval(state.autoTimer);
      state.autoTimer = null;
      document.getElementById("autoSearch").disabled = false;
      state.autoResult = job.result;
      state.autoPage = 1;
      progressBar.style.width = "100%";
      status.className = "status ok";
      status.textContent = job.from_cache
        ? "Результат автоподбора загружен из кэша."
        : "Автоподбор завершён.";
      renderAutoBest();
      renderAutoTable();
    } else if (job.status === "error") {
      clearInterval(state.autoTimer);
      state.autoTimer = null;
      document.getElementById("autoSearch").disabled = false;
      status.className = "status error";
      status.textContent = `Ошибка автоподбора: ${job.error}`;
      toast(`Ошибка автоподбора: ${job.error}`, true);
    }
  } catch (error) {
    clearInterval(state.autoTimer);
    state.autoTimer = null;
    document.getElementById("autoSearch").disabled = false;
    status.className = "status error";
    status.textContent = `Ошибка опроса задания: ${error.message}`;
  }
}

function renderMeta() {
  const meta = state.meta;
  document.getElementById("methodBadge").textContent =
    "Сценарии: BlocksNet competitive_provision · автоподбор: min-cost-flow";
  const validation = meta.validation || {};
  const badge = document.getElementById("validationBadge");
  if (validation.passed) {
    badge.textContent = `Сверка с практикой 2: пройдена (${fmtInt(validation.blocks)} кварталов)`;
    badge.className = "badge badge-green";
  } else {
    badge.textContent = "Сверка с практикой 2: не пройдена";
    badge.className = "badge badge-red";
  }

  const sources = document.getElementById("sourcesBlock");
  const sourceItems = (meta.sources || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const assumptionItems = (meta.assumptions || []).map((item) => `<li>${esc(item)}</li>`).join("");
  sources.innerHTML = `
    <h3>Источники данных</h3>
    <ul>${sourceItems}</ul>
    <h3>Допущения</h3>
    <ul>${assumptionItems}</ul>
    <p class="legend-note">Приложение сравнивает обеспеченность населения местами в больницах для разных вариантов размещения. Выбор земельного участка и оценка возможности строительства требуют отдельного градостроительного обоснования.</p>
  `;
  document.getElementById("autoCapacity").value = meta.default_auto_capacity;
}

async function loadSavedScenarios() {
  state.savedScenarios = await fetchJson("/api/scenarios");
  renderSavedScenarios();
  renderCompareChecklist();
  renderCompareTable();
}

function renderSavedScenarios() {
  const container = document.getElementById("savedScenarios");
  if (!state.savedScenarios.length) {
    container.className = "saved-list muted";
    container.textContent = "Сценариев пока нет";
    return;
  }
  container.className = "saved-list";
  container.innerHTML = state.savedScenarios
    .map((scenario) => {
      const summary = scenario.summary || {};
      const hospitals = (scenario.hospitals || [])
        .map((item) => `кв. ${item.block_id}: ${fmtInt(item.capacity)}`)
        .join("; ");
      return `
        <div class="saved-item">
          <div class="saved-head">
            <strong>${esc(scenario.name)}</strong>
            <span class="saved-meta">${new Date(scenario.created_at).toLocaleString("ru-RU")}</span>
          </div>
          <div class="saved-meta">${esc(hospitals)}</div>
          <div class="saved-meta">Дефицит: ${fmtInt(summary.deficit)} мест · сокращение: ${fmtInt(
            summary.deficit_reduction,
          )} мест · обеспеченность: ${fmtPct(summary.provision_total)}</div>
          <div class="saved-actions">
            <button type="button" class="btn btn-secondary" data-load="${scenario.id}">Загрузить</button>
            <button type="button" class="btn btn-danger" data-delete="${scenario.id}">Удалить</button>
          </div>
        </div>
      `;
    })
    .join("");

  container.querySelectorAll("[data-load]").forEach((button) => {
    button.addEventListener("click", () => loadScenario(button.dataset.load));
  });
  container.querySelectorAll("[data-delete]").forEach((button) => {
    button.addEventListener("click", () => removeScenario(button.dataset.delete));
  });
}

function renderCompareChecklist() {
  const container = document.getElementById("compareChecklist");
  if (!state.savedScenarios.length) {
    container.className = "compare-checklist muted";
    container.textContent = "Сохранённых сценариев нет";
    return;
  }
  container.className = "compare-checklist";
  container.innerHTML = state.savedScenarios
    .map(
      (scenario) => `
      <label>
        <input type="checkbox" data-compare="${scenario.id}" ${
          state.compareSelected.has(scenario.id) ? "checked" : ""
        }>
        ${esc(scenario.name)}
      </label>
    `,
    )
    .join("");
  container.querySelectorAll("[data-compare]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.compareSelected.add(checkbox.dataset.compare);
      else state.compareSelected.delete(checkbox.dataset.compare);
      renderCompareTable();
    });
  });
}

function renderCompareTable() {
  const table = document.getElementById("compareTable");
  const selected = state.savedScenarios.filter((scenario) => state.compareSelected.has(scenario.id));
  table.querySelector("thead").innerHTML = `
    <tr>
      <th>Сценарий</th>
      <th>Больницы</th>
      <th>Добавлено мест</th>
      <th>Дефицит, мест</th>
      <th>Сокращение дефицита, мест</th>
      <th>Обеспеченность</th>
      <th>Вместимость сети, мест</th>
    </tr>
  `;
  if (!selected.length) {
    table.querySelector("tbody").innerHTML = `<tr><td colspan="7" class="na">Отметьте сценарии выше</td></tr>`;
    return;
  }
  table.querySelector("tbody").innerHTML = selected
    .map((scenario) => {
      const summary = scenario.summary || {};
      const hospitals = (scenario.hospitals || [])
        .map((item) => `${item.block_id} (${fmtInt(item.capacity)})`)
        .join(", ");
      return `
        <tr>
          <td>${esc(scenario.name)}</td>
          <td>${esc(hospitals)}</td>
          <td>${fmtInt(summary.capacity_added)}</td>
          <td>${fmtInt(summary.deficit)}</td>
          <td class="positive">${fmtInt(summary.deficit_reduction)}</td>
          <td>${fmtPct(summary.provision_total)}</td>
          <td>${fmtInt(summary.total_capacity)}</td>
        </tr>
      `;
    })
    .join("");
}

async function loadScenario(scenarioId) {
  const scenario = state.savedScenarios.find((item) => item.id === scenarioId);
  if (!scenario) return;
  state.scenarioHospitals = {};
  for (const item of scenario.hospitals || []) {
    mergeHospital(item.block_id, item.capacity);
  }
  renderPlannedHospitals();
  renderNewHospitalMarkers();
  const firstBlock = scenario.hospitals && scenario.hospitals.length ? scenario.hospitals[0].block_id : null;
  if (firstBlock !== null) selectBlock(firstBlock, false);
  await calculateScenario();
  toast(`Сценарий «${scenario.name}» загружен.`);
}

async function removeScenario(scenarioId) {
  try {
    await fetchJson(`/api/scenarios/${scenarioId}`, { method: "DELETE" });
    state.compareSelected.delete(scenarioId);
    await loadSavedScenarios();
    toast("Сценарий удалён.");
  } catch (error) {
    toast(`Не удалось удалить сценарий: ${error.message}`, true);
  }
}

async function saveCurrentScenario() {
  const name = document.getElementById("scenarioName").value.trim();
  if (!name) {
    toast("Укажите название сценария.", true);
    return;
  }
  if (!state.scenarioResult) {
    toast("Сначала рассчитайте сценарий.", true);
    return;
  }
  const hospitals = Object.entries(state.scenarioHospitals).map(([blockId, capacity]) => ({
    block_id: Number(blockId),
    capacity: Number(capacity),
  }));
  if (!hospitals.length) {
    toast("В сценарии нет ни одной новой больницы.", true);
    return;
  }
  try {
    await fetchJson("/api/scenarios", {
      method: "POST",
      body: JSON.stringify({ name, hospitals, summary: state.scenarioResult.summary }),
    });
    document.getElementById("scenarioName").value = "";
    await loadSavedScenarios();
    toast("Сценарий сохранён.");
  } catch (error) {
    toast(`Не удалось сохранить сценарий: ${error.message}`, true);
  }
}

function csvCell(value) {
  if (value === null || value === undefined) return '""';
  return `"${String(value).replaceAll('"', '""')}"`;
}

function downloadCsv(filename, header, rows) {
  const lines = [header.map(csvCell).join(";"), ...rows.map((row) => row.map(csvCell).join(";"))];
  const blob = new Blob([`\ufeff${lines.join("\r\n")}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function exportBlocksCsv() {
  const rows = blockRows().map((row) => [
    row.block_id,
    row.population,
    row.demand,
    row.capacity_original,
    row.capacity_added,
    row.capacity,
    row.provision_original,
    row.provision,
    row.deficit_original,
    row.deficit,
    Number(row.provision) - Number(row.provision_original),
    Number(row.deficit) - Number(row.deficit_original),
  ]);
  downloadCsv(
    state.scenarioResult ? "scenario_blocks.csv" : "original_blocks.csv",
    [
      "block_id",
      "population",
      "demand",
      "capacity_original",
      "capacity_added",
      "capacity_scenario",
      "provision_original",
      "provision_scenario",
      "deficit_original",
      "deficit_scenario",
      "provision_change",
      "deficit_change",
    ],
    rows,
  );
}

function exportAutoCsv() {
  if (!state.autoResult) return;
  const rows = state.autoResult.rows.map((row) => [
    row.block_id,
    row.added_capacity,
    row.deficit_reduction,
    row.deficit,
    1 - Number(row.deficit) / state.meta.total_demand,
    row.population,
    row.own_demand,
    row.own_deficit,
    row.existing_capacity,
    state.autoResult.best_blocks.includes(Number(row.block_id)) ? "да" : "нет",
  ]);
  downloadCsv(
    `auto_capacity_${state.autoResult.capacity}.csv`,
    [
      "block_id",
      "added_capacity",
      "deficit_reduction",
      "deficit_after",
      "provision_after",
      "population",
      "own_demand",
      "own_deficit",
      "existing_capacity",
      "best_tie",
    ],
    rows,
  );
}

function setupEvents() {
  document.querySelectorAll("#mapMode button").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });

  document.getElementById("addHospital").addEventListener("click", () => {
    if (state.selectedBlock === null) {
      toast("Сначала выберите квартал на карте.", true);
      return;
    }
    const capacity = Number(document.getElementById("manualCapacity").value);
    if (!Number.isInteger(capacity) || capacity <= 0) {
      toast("Вместимость больницы должна быть положительным целым числом.", true);
      return;
    }
    mergeHospital(state.selectedBlock, capacity);
    renderPlannedHospitals();
    renderNewHospitalMarkers();
    toast(`Добавлено: квартал ${state.selectedBlock}, ${fmtInt(capacity)} мест.`);
  });

  document.getElementById("calculateScenario").addEventListener("click", calculateScenario);
  document.getElementById("resetScenario").addEventListener("click", resetScenario);
  document.getElementById("autoSearch").addEventListener("click", startAutoSearch);
  document.getElementById("saveScenario").addEventListener("click", saveCurrentScenario);
  document.getElementById("exportBlocks").addEventListener("click", exportBlocksCsv);
  document.getElementById("exportAuto").addEventListener("click", exportAutoCsv);

  document.getElementById("blockFilter").addEventListener("input", (event) => {
    state.blockFilter = event.target.value;
    state.blockPage = 1;
    renderBlockTable();
  });
  document.getElementById("autoFilter").addEventListener("input", (event) => {
    state.autoFilter = event.target.value;
    state.autoPage = 1;
    renderAutoTable();
  });

  document.querySelectorAll("#resultTabs button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("#resultTabs button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      document.querySelectorAll(".tab-page").forEach((page) => page.classList.remove("active"));
      document.getElementById(`tab-${button.dataset.tab}`).classList.add("active");
      if (button.dataset.tab === "auto") renderAutoTable();
      if (button.dataset.tab === "compare") {
        renderCompareChecklist();
        renderCompareTable();
      }
    });
  });
}

async function init() {
  try {
    state.meta = await fetchJson("/api/meta");
    renderMeta();
    updateLegend();
    renderPlannedHospitals();
    renderKpis();
    renderSelectedCard();
    renderBlockTable();
    renderAutoTable();
    setupEvents();

    const [blocksGeo, hospitalsGeo, boundariesGeo, roadsGeo, waterGeo] = await Promise.all([
      fetchJson("/api/blocks.geojson"),
      fetchJson("/api/hospitals.geojson"),
      fetchJson("/api/boundaries.geojson"),
      fetchJson("/api/roads.geojson"),
      fetchJson("/api/water.geojson"),
    ]);
    setupMap(blocksGeo, hospitalsGeo, boundariesGeo, roadsGeo, waterGeo);
    updateLegend();
    refreshBlockStyles();
    renderBlockTable();
    renderSelectedCard();
    await loadSavedScenarios();
  } catch (error) {
    document.getElementById("methodBadge").textContent = "Ошибка загрузки данных";
    document.getElementById("methodBadge").className = "badge badge-red";
    toast(`Не удалось загрузить приложение: ${error.message}`, true);
  }
}

document.addEventListener("DOMContentLoaded", init);

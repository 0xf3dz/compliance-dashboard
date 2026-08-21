<script setup lang="ts">
import type { ChartData, ChartOptions } from "chart.js";
import { computed } from "vue";
import { Bar, Line } from "vue-chartjs";
import { api, type IncidentSummary } from "../api";
import { usePanelCollapse } from "../usePanelCollapse";
import { useResource } from "../useResource";

const summary = useResource<IncidentSummary>(api.incidentSummary);

const { open, persist } = usePanelCollapse("incidents");

const TYPE_LABELS: Record<string, string> = {
  VEH: "Vehicle / plant",
  DUS: "Dust / air quality",
  ENV: "Environmental",
  EQP: "Equipment",
  SLP: "Slip / trip / fall",
  ELE: "Electrical",
  OTH: "Other",
};

const axis = {
  x: { grid: { color: "#2c3742" }, ticks: { color: "#93a1b0" } },
  y: {
    grid: { color: "#2c3742" },
    ticks: { color: "#93a1b0", precision: 0 },
    beginAtZero: true,
  },
};

const trendData = computed<ChartData<"line">>(() => {
  const rows = summary.data.value?.trend ?? [];
  return {
    labels: rows.map((r) => r.month),
    datasets: [
      {
        label: "Incidents",
        data: rows.map((r) => r.count),
        borderColor: "#f0883e",
        backgroundColor: "rgba(240,136,62,0.2)",
        fill: true,
        tension: 0.3,
        pointRadius: 2,
      },
    ],
  };
});

const trendOptions: ChartOptions<"line"> = {
  responsive: true,
  maintainAspectRatio: false,
  scales: axis,
  plugins: { legend: { display: false } },
};

const typeData = computed<ChartData<"bar">>(() => {
  const rows = summary.data.value?.by_type ?? [];
  return {
    labels: rows.map((r) => TYPE_LABELS[r.key] ?? r.key),
    datasets: [
      {
        label: "By type",
        data: rows.map((r) => r.count),
        backgroundColor: "#4d9de0",
      },
    ],
  };
});

const severityData = computed<ChartData<"bar">>(() => {
  const rows = summary.data.value?.by_severity ?? [];
  const colour: Record<string, string> = {
    Low: "#3fb950",
    Medium: "#e3b341",
    High: "#f85149",
  };
  return {
    labels: rows.map((r) => r.key),
    datasets: [
      {
        label: "By severity",
        data: rows.map((r) => r.count),
        backgroundColor: rows.map((r) => colour[r.key] ?? "#93a1b0"),
      },
    ],
  };
});

const barOptions: ChartOptions<"bar"> = {
  responsive: true,
  maintainAspectRatio: false,
  scales: axis,
  plugins: { legend: { display: false } },
};
</script>

<template>
  <details class="panel" :open="open" @toggle="persist">
    <summary>
      <h2>Safety incidents</h2>
      <p class="subtitle">Monthly trend and breakdowns by type and normalised severity.</p>
    </summary>

    <div v-if="summary.loading.value" class="state">Loading…</div>
    <div v-else-if="summary.error.value" class="state error">
      No data / API unavailable ({{ summary.error.value }}).
    </div>
    <template v-else-if="summary.data.value">
      <div class="kpis">
        <div class="kpi">
          <div class="label">Total incidents</div>
          <div class="value">{{ summary.data.value.total }}</div>
          <div class="sub">across {{ summary.data.value.trend.length }} months</div>
        </div>
      </div>

      <div class="section-title">Monthly trend</div>
      <div class="chart-wrap">
        <Line :data="trendData" :options="trendOptions" />
      </div>

      <div class="two-col" style="margin-top: 20px">
        <div>
          <div class="section-title">By type</div>
          <div class="chart-wrap" style="height: 260px">
            <Bar :data="typeData" :options="barOptions" />
          </div>
        </div>
        <div>
          <div class="section-title">By severity</div>
          <div class="chart-wrap" style="height: 260px">
            <Bar :data="severityData" :options="barOptions" />
          </div>
        </div>
      </div>
    </template>
  </details>
</template>

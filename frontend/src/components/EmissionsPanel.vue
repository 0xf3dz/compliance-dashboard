<script setup lang="ts">
import type { ChartData, ChartOptions } from "chart.js";
import { computed } from "vue";
import { Chart } from "vue-chartjs";
import { api, type EmissionsSummary, type MonthlyEmissions } from "../api";
import { usePanelCollapse } from "../usePanelCollapse";
import { useResource } from "../useResource";

const monthly = useResource<MonthlyEmissions[]>(api.emissionsMonthly);
const summary = useResource<EmissionsSummary>(api.emissionsSummary);

const { open, persist } = usePanelCollapse("emissions");

const SCOPE1 = "#f0883e";
// The same orange at low saturation. A month without a fuel invoice keeps its
// place in the chart, and the muted bar says the value is a floor.
const SCOPE1_INCOMPLETE = "#7a5233";

function tonnes(kg: number): string {
  return (kg / 1000).toLocaleString("en-AU", { maximumFractionDigits: 0 });
}

const incompleteMonths = computed(() =>
  (monthly.data.value ?? []).filter((r) => !r.scope1_has_deliveries).map((r) => r.month),
);

const chartData = computed<ChartData<"bar" | "line">>(() => {
  const rows = monthly.data.value ?? [];
  return {
    labels: rows.map((r) => (r.scope1_has_deliveries ? r.month : `${r.month} (incomplete)`)),
    datasets: [
      {
        type: "bar",
        label: "Scope 1 (fuel)",
        data: rows.map((r) => Math.round(r.scope1_kg / 1000)),
        backgroundColor: rows.map((r) => (r.scope1_has_deliveries ? SCOPE1 : SCOPE1_INCOMPLETE)),
        stack: "emissions",
      },
      {
        type: "bar",
        label: "Scope 2 (electricity)",
        data: rows.map((r) => Math.round(r.scope2_kg / 1000)),
        backgroundColor: "#4d9de0",
        stack: "emissions",
      },
      {
        type: "line",
        label: "Total",
        data: rows.map((r) => Math.round(r.total_kg / 1000)),
        borderColor: "#e6edf3",
        backgroundColor: "#e6edf3",
        tension: 0.3,
        pointRadius: 2,
      },
    ],
  };
});

const chartOptions: ChartOptions<"bar" | "line"> = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  scales: {
    x: { stacked: true, grid: { color: "#2c3742" }, ticks: { color: "#93a1b0" } },
    y: {
      stacked: true,
      grid: { color: "#2c3742" },
      ticks: { color: "#93a1b0" },
      title: { display: true, text: "tCO₂e", color: "#93a1b0" },
    },
  },
  plugins: { legend: { labels: { color: "#e6edf3" } } },
};
</script>

<template>
  <details class="panel" :open="open" @toggle="persist">
    <summary>
      <h2>Emissions</h2>
      <p class="subtitle">Monthly Scope 1 and Scope 2, computed from cleaned activity data.</p>
    </summary>

    <div v-if="summary.loading.value" class="state">Loading…</div>
    <div v-else-if="summary.error.value" class="state error">
      No data / API unavailable ({{ summary.error.value }}).
    </div>
    <template v-else-if="summary.data.value">
      <div class="kpis">
        <div class="kpi">
          <div class="label">Total emissions</div>
          <div class="value">{{ tonnes(summary.data.value.total_kg) }} t</div>
          <div class="sub">CO₂e over {{ summary.data.value.months }} months</div>
        </div>
        <div class="kpi">
          <div class="label">Scope 1 — fuel</div>
          <div class="value">{{ tonnes(summary.data.value.scope1_kg) }} t</div>
          <div class="sub">{{ Math.round(summary.data.value.scope1_share * 100) }}% of total</div>
        </div>
        <div class="kpi">
          <div class="label">Scope 2 — electricity</div>
          <div class="value">{{ tonnes(summary.data.value.scope2_kg) }} t</div>
          <div class="sub">{{ Math.round(summary.data.value.scope2_share * 100) }}% of total</div>
        </div>
      </div>

      <!-- The reported figure beside what it may understate. The two numbers
           answer one question: the left number is the one to report, the right
           number is the work to do before the next report.

           The server composes every figure, label, method note and action
           sentence from data_quality_issues. This file holds no meter, month or
           incident name, so a gap leaves this panel when the issue behind it
           clears, in the same way as the caveats below. -->
      <div v-if="summary.data.value.exposure.items.length" class="exposure">
        <div class="exposure__figs">
          <div class="exposure__fig">
            <div class="label">Reported</div>
            <div class="value">{{ tonnes(summary.data.value.total_kg) }} t</div>
            <div class="sub">Report this figure.</div>
          </div>
          <div class="exposure__fig exposure__fig--gap">
            <div class="label">Possibly missing</div>
            <div class="value">+{{ tonnes(summary.data.value.exposure.kg_mid) }} t</div>
            <div class="sub">
              {{ Math.round(summary.data.value.exposure.share_of_reported * 1000) / 10 }}% of
              the reported total · range +{{ tonnes(summary.data.value.exposure.kg_low) }} to
              +{{ tonnes(summary.data.value.exposure.kg_high) }} t
            </div>
          </div>
        </div>

        <div
          v-for="item in summary.data.value.exposure.items"
          :key="item.code"
          class="gap"
          :class="{ 'gap--none': item.kg_mid === 0 }"
        >
          <div class="gap__head">
            <span class="gap__amount">{{
              item.kg_mid > 0 ? `+${tonnes(item.kg_mid)} t` : "No gap"
            }}</span>
            <span class="gap__label">{{ item.label }}</span>
            <span v-if="item.scope" class="badge">Scope {{ item.scope }}</span>
          </div>
          <div class="gap__action">
            {{ item.action || "Do no work. The reading is correct." }}
          </div>
          <details class="drill">
            <summary>Method and evidence</summary>
            <p class="gap__note">{{ item.method_note }}</p>
            <p class="gap__evidence">{{ item.evidence }}</p>
            <p class="gap__meta">
              {{ item.months.length }} month{{ item.months.length === 1 ? "" : "s" }}:
              {{ item.months.join(", ") }} · method <code>{{ item.method }}</code>
            </p>
          </details>
        </div>
      </div>

      <div class="chart-wrap">
        <Chart type="bar" :data="chartData" :options="chartOptions" />
      </div>

      <p v-if="incompleteMonths.length" class="subtitle">
        A muted Scope 1 bar and an <em>(incomplete)</em> label mark a month with no fuel invoice:
        {{ incompleteMonths.join(", ") }}. Scope 1 for such a month is a lower bound, not zero.
      </p>

      <!-- Every caveat comes from the server, which builds it from
           data_quality_issues. No month, meter or incident is named here. -->
      <div v-if="summary.data.value.caveats.length" class="caveats">
        <div v-for="c in summary.data.value.caveats" :key="c.code" class="caveat">
          <span class="code">{{ c.code }}</span>
          <span class="text">{{ c.message }}</span>
          <span class="months">{{ c.months.join(", ") }}</span>
        </div>
      </div>
    </template>
  </details>
</template>

<script setup lang="ts">
import AiFindingsPanel from "./components/AiFindingsPanel.vue";
import DataQualityPanel from "./components/DataQualityPanel.vue";
import EmissionsPanel from "./components/EmissionsPanel.vue";
import IncidentsPanel from "./components/IncidentsPanel.vue";
</script>

<template>
  <div class="app">
    <header class="app-header">
      <h1>Compliance Intelligence: Ironbark Ridge Resources</h1>
      <p>18 months of operational data (Jan 2025 – Jun 2026): emissions, safety, data quality.</p>
    </header>

    <EmissionsPanel />
    <IncidentsPanel />
    <AiFindingsPanel />
    <DataQualityPanel />
  </div>
</template>

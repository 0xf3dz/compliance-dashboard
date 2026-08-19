<script setup lang="ts">
import { api, type DataQualityReport } from "../api";
import { useResource } from "../useResource";

const report = useResource<DataQualityReport>(api.dataQuality);
</script>

<template>
  <section class="panel">
    <h2>Data quality report</h2>
    <p class="subtitle">
      Every problem found in the source files, each fixed, flagged, or rejected — never
      silently discarded. Open a group to read every row it holds.
    </p>

    <div v-if="report.loading.value" class="state">Loading…</div>
    <div v-else-if="report.error.value" class="state error">
      No data / API unavailable ({{ report.error.value }}).
    </div>
    <template v-else-if="report.data.value">
      <div class="kpis">
        <div class="kpi">
          <div class="label">Total issues logged</div>
          <div class="value">{{ report.data.value.total }}</div>
          <div class="sub">across {{ report.data.value.files.length }} source files</div>
        </div>
      </div>

      <div v-for="file in report.data.value.files" :key="file.source_file">
        <div class="section-title">{{ file.source_file }} ({{ file.count }})</div>
        <table>
          <thead>
            <tr>
              <th>Issue type</th>
              <th>Action</th>
              <th style="text-align: right">Count</th>
              <th>Rows</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="g in file.issue_types" :key="g.issue_type">
              <td>{{ g.issue_type }}</td>
              <td><span class="badge" :class="g.action">{{ g.action }}</span></td>
              <td style="text-align: right">{{ g.count }}</td>
              <td>
                <!-- The draft printed g.items[0] and stopped, so 12 of 77 rows
                     reached the reader. The expander holds the whole array. -->
                <details class="drill">
                  <summary>Show all {{ g.count }}</summary>
                  <table>
                    <thead>
                      <tr>
                        <th>Record</th>
                        <th>Field</th>
                        <th>Raw value</th>
                        <th>Resolution</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="item in g.items" :key="item.id">
                        <td class="ref">{{ item.record_ref ?? `row ${item.source_row ?? "—"}` }}</td>
                        <td>{{ item.field ?? "—" }}</td>
                        <td>{{ item.raw_value ?? "—" }}</td>
                        <td style="color: var(--muted)">{{ item.resolution ?? "—" }}</td>
                        <td style="color: var(--muted)">{{ item.detail ?? "—" }}</td>
                      </tr>
                    </tbody>
                  </table>
                </details>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

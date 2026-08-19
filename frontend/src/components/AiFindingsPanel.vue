<script setup lang="ts">
import { computed } from "vue";
import { api, type AiFinding } from "../api";
import { useResource } from "../useResource";

const findings = useResource<AiFinding[]>(api.aiFindings);

interface Highlighted {
  before: string;
  quote: string;
  after: string;
}

// Split every description around its verbatim quote once, keyed by finding id.
// The draft called a highlight() function three times per finding from the
// template, so each render repeated the same string search for every row.
// The grounding guard in ingestion/ai/classify.py guarantees the quote is a
// substring; the index check below covers the empty quote alone.
const highlights = computed<Record<number, Highlighted>>(() => {
  const out: Record<number, Highlighted> = {};
  for (const f of findings.data.value ?? []) {
    const idx = f.evidence_quote ? f.description.indexOf(f.evidence_quote) : -1;
    out[f.id] =
      idx < 0
        ? { before: f.description, quote: "", after: "" }
        : {
            before: f.description.slice(0, idx),
            quote: f.evidence_quote,
            after: f.description.slice(idx + f.evidence_quote.length),
          };
  }
  return out;
});

const psychosocial = computed(() => (findings.data.value ?? []).filter((f) => f.is_psychosocial));
const mismatches = computed(() => (findings.data.value ?? []).filter((f) => f.severity_mismatch));

// Every classification, not the flagged subset. The assignment asks for a
// category on each incident, so each one must be visible somewhere.
const categories = computed(() => {
  const counts: Record<string, number> = {};
  for (const f of findings.data.value ?? []) {
    counts[f.ai_category] = (counts[f.ai_category] ?? 0) + 1;
  }
  const rows = Object.entries(counts).map(([name, count]) => ({ name, count }));
  rows.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const max = rows.length ? rows[0].count : 1;
  return rows.map((r) => ({ ...r, width: `${Math.round((r.count / max) * 100)}%` }));
});

// An incident the original coder filed as OTH, which the model placed in a
// real category. This is where a hidden hazard hides in the source data.
const recategorised = computed(() =>
  (findings.data.value ?? []).filter((f) => f.type_code === "OTH" && f.ai_category !== "Other"),
);
</script>

<template>
  <section class="panel">
    <h2>AI incident findings</h2>
    <p class="subtitle">
      Claude classifies free-text descriptions and flags hidden psychosocial hazards and
      severity mismatches. Every finding cites a verbatim quote from the source record.
    </p>

    <div v-if="findings.loading.value" class="state">Loading…</div>
    <div v-else-if="findings.error.value" class="state error">
      No data / API unavailable ({{ findings.error.value }}).
    </div>
    <div v-else-if="(findings.data.value ?? []).length === 0" class="state">
      No AI findings yet. Run <code>python -m ingestion.ai.classify</code> with a valid
      ANTHROPIC_API_KEY to populate this panel. Findings are never fabricated.
    </div>
    <template v-else>
      <div class="section-title">
        Category distribution ({{ (findings.data.value ?? []).length }} incidents)
      </div>
      <div v-for="c in categories" :key="c.name" class="cat-row">
        <span class="name">{{ c.name }}</span>
        <span class="track"><span class="fill" :style="{ width: c.width }"></span></span>
        <span class="count">{{ c.count }}</span>
      </div>

      <div class="section-title">Recategorised from "Other" ({{ recategorised.length }})</div>
      <div v-for="f in recategorised" :key="`r-${f.id}`" class="finding">
        <div class="head">
          <span class="id">{{ f.incident_id }}</span>
          <span style="color: var(--muted)">recorded: {{ f.type_code }} →</span>
          <span class="badge flagged">{{ f.ai_category }}</span>
          <span v-if="f.stale" class="badge stale">Stale</span>
          <span style="color: var(--muted)">{{ f.incident_date }} · {{ f.location }}</span>
        </div>
        <p class="desc">
          <template v-if="highlights[f.id].quote">
            {{ highlights[f.id].before }}<mark>{{ highlights[f.id].quote }}</mark
            >{{ highlights[f.id].after }}
          </template>
          <template v-else>{{ f.description }}</template>
        </p>
        <p class="rationale">{{ f.rationale }}</p>
      </div>

      <div class="section-title">Psychosocial hazards ({{ psychosocial.length }})</div>
      <div v-for="f in psychosocial" :key="f.id" class="finding">
        <div class="head">
          <span class="id">{{ f.incident_id }}</span>
          <span class="badge psycho">Psychosocial</span>
          <span class="badge flagged">{{ f.ai_category }}</span>
          <span v-if="f.stale" class="badge stale">Stale</span>
          <span style="color: var(--muted)">{{ f.incident_date }} · {{ f.location }}</span>
        </div>
        <p class="desc">
          <template v-if="highlights[f.id].quote">
            {{ highlights[f.id].before }}<mark>{{ highlights[f.id].quote }}</mark
            >{{ highlights[f.id].after }}
          </template>
          <template v-else>{{ f.description }}</template>
        </p>
        <p class="rationale">{{ f.rationale }}</p>
      </div>

      <div class="section-title">Severity mismatches ({{ mismatches.length }})</div>
      <div v-for="f in mismatches" :key="`m-${f.id}`" class="finding">
        <div class="head">
          <span class="id">{{ f.incident_id }}</span>
          <span class="badge mismatch">Severity understated</span>
          <span v-if="f.stale" class="badge stale">Stale</span>
          <span style="color: var(--muted)">recorded: {{ f.severity_norm }}</span>
        </div>
        <p class="desc">
          <template v-if="highlights[f.id].quote">
            {{ highlights[f.id].before }}<mark>{{ highlights[f.id].quote }}</mark
            >{{ highlights[f.id].after }}
          </template>
          <template v-else>{{ f.description }}</template>
        </p>
        <p class="rationale">{{ f.mismatch_detail ?? f.rationale }}</p>
      </div>
    </template>
  </section>
</template>

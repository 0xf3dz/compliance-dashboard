<script setup lang="ts">
import { computed, ref } from "vue";
import { api } from "../api";
import { useResource } from "../useResource";

// The footer answers the questions a reader asks at the end of the page: what
// data is this, how old is it, and how should I read the numbers. The facts
// come from the API, so the footer stays true when the data changes. The two
// static lines match the method note in the write-up.
const report = useResource(api.dataQuality);
const monthly = useResource(api.emissionsMonthly);

const period = computed(() => {
  const rows = monthly.data.value ?? [];
  if (rows.length === 0) return null;
  return { from: rows[0].month, to: rows[rows.length - 1].month, months: rows.length };
});

const ingested = computed(() => {
  const at = report.data.value?.ingested_at;
  if (!at) return null;
  return new Date(at).toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" });
});

// The request form posts to the API, which stores the request in the
// feature_requests table. A footer form with nowhere to send the text would
// be a prop.
const name = ref("");
const email = ref("");
const message = ref("");
const sending = ref(false);
const sent = ref(false);
const sendError = ref<string | null>(null);

async function submit() {
  sending.value = true;
  sendError.value = null;
  try {
    await api.requestFeature({
      name: name.value,
      email: email.value,
      message: message.value,
    });
    sent.value = true;
  } catch (err) {
    sendError.value = err instanceof Error ? err.message : "send failed";
  } finally {
    sending.value = false;
  }
}

// Confirm these two URLs before you share the repository link.
const LINKEDIN_URL = "https://www.linkedin.com/in/federico-scandizzo";
const X_URL = "https://x.com/0xf3dz";
</script>

<template>
  <footer class="app-footer">
    <!-- No error state: a footer that cannot load degrades to the static
         lines instead of adding a red box under a working page. -->
    <p v-if="period && report.data.value" class="app-footer__line">
      <!-- The issue report names only the files that have issues, so the file
           count is fixed text: the pipeline reads five files. -->
      Source data: five CSV files, {{ period.from }} to {{ period.to }}
      ({{ period.months }} months)<template v-if="ingested">, ingested {{ ingested }}</template>.
      {{ report.data.value.total }} data quality issues logged.
    </p>
    <p class="app-footer__line">
      Emission factors are indicative values for this exercise.
    </p>

    <details class="drill request">
      <summary>Request additional features</summary>
      <form v-if="!sent" class="request__form" @submit.prevent="submit">
        <label>
          Name
          <input v-model="name" type="text" required maxlength="200" />
        </label>
        <label>
          Email
          <input v-model="email" type="email" required maxlength="320" />
        </label>
        <label>
          Inquiry
          <textarea v-model="message" rows="3" required maxlength="5000"></textarea>
        </label>
        <button type="submit" :disabled="sending">
          {{ sending ? "Sending…" : "Send request" }}
        </button>
        <p v-if="sendError" class="state error">Request not sent ({{ sendError }}).</p>
      </form>
      <p v-else class="app-footer__line">
        Request sent. Thank you.
      </p>
    </details>

    <p class="app-footer__line">
      This site is a take-home assignment for a software engineering role.
    </p>
    <p class="app-footer__line">
      Developed by Federico Scandizzo ·
      <a :href="LINKEDIN_URL" target="_blank" rel="noopener">LinkedIn</a> ·
      <a :href="X_URL" target="_blank" rel="noopener">Twitter</a>
    </p>
    <p class="app-footer__line">© 2026 ESGAgent. All rights reserved.</p>
  </footer>
</template>

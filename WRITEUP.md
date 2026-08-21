# Ironbark Ridge Compliance Intelligence: write-up

This app turns five messy operational CSVs into trusted emissions, safety, and
data-quality intelligence. It has four layers: a Python and pandas ingestion
pipeline into PostgreSQL, a Node and TypeScript API, an Anthropic Claude
classification step, and a Vue 3 dashboard.

## Run it live

The app runs at https://ironbark-intelligence.up.railway.app/. This is a
temporary demo. To run a permanent copy on your own machine, follow "How to run" below.

The deployment is one Docker image. The root `Dockerfile` builds the Vue
frontend and runs the Node API that serves it. The app answers on one origin as
one Railway web service. PostgreSQL is a separate Railway database, reached
through `DATABASE_URL`. The frontend build reads `VITE_API_BASE`, which is empty
in the image, so the browser calls `/api` on the same host.

## How to run

Prerequisites: Docker, Python 3.11, and Node 20 or later.

1. Clone the repository. Change into the new directory. Run every later command
   from this directory.

   ```bash
   git clone https://github.com/0xf3dz/compliance-dashboard.git
   cd compliance-dashboard
   ```


2. Write the environment file. Copy `.env.example` to `.env` at the repository
   root and fill in the values. Both `ingestion/db.py` and `api/src/db.ts` need
   this file.

   ```bash
   cp .env.example .env
   # then edit .env and set:
   ANTHROPIC_API_KEY=sk-ant-...
   DATABASE_URL=postgresql://ironbark:ironbark@localhost:5544/ironbark
   ```

3. Start the database.

   ```bash
   docker compose up -d
   docker compose ps          # wait until db is healthy
   ```

4. Install the Python tools and run the ingestion pipeline.

   ```bash
   python -m venv .venv && . .venv/bin/activate
   pip install -r ingestion/requirements.txt
   python -m ingestion.ingest
   ```

5. Run the Python tests.

   ```bash
   python -m pytest ingestion/tests -q      # 68 tests
   ```

6. Run the AI classification step. This step needs a valid `ANTHROPIC_API_KEY`.

   ```bash
   python -m ingestion.ai.classify          # add --force to reclassify
   ```

7. Start the API and run its tests.

   ```bash
   cd api && npm install
   npm run typecheck
   npm test                   # 58 tests, includes the 226,881 kg anchor
   npm run dev                # http://localhost:3000
   ```

8. Start the dashboard.

   ```bash
   cd frontend && npm install
   npm run dev                # http://localhost:5173
   ```

Continuous integration runs the same order on every push: `pytest`, ingest,
API typecheck and tests, then the frontend build, against a `postgres:16`
service container. See `.github/workflows/ci.yml`. CI does not run the AI step,
because a required secret would break the workflow for anyone who forks the
repository.

## Data problems and solutions

The pipeline finds 20 distinct problems and logs 77 issue rows across the five
files, under 16 issue types. The table below lists each problem, the action, and
the reason.

| # | File | Problem | Action | Reason |
|---|------|---------|--------|--------|
| 1 | fuel_deliveries | Header names carry stray spaces | fixed (not logged) | Header whitespace is a parse concern. The loader strips it. |
| 2 | fuel_deliveries | Four date formats, including `Mon-YY` | flagged (`imputed_date`, 26 rows) | Parse all formats. `DD/MM/YYYY` is day-first (Australian). `Mon-YY` has no day, so impute the first of the month and flag it. |
| 3 | fuel_deliveries | Mixed units `L`, `litres`, `kL` | fixed (`unit_conversion`, 11 rows) | Normalise to litres. Multiply `kL` by 1000. Log each conversion so the 1000x change is traceable. |
| 4 | fuel_deliveries | Cost mixes `$182,946.64` and `132182.58` | fixed (not logged) | Strip `$` and commas. This is a format concern only. |
| 5 | fuel_deliveries | `INV-41777`: quantity −12,500 L, cost −$23,375 | flagged (`negative_quantity`, 1 row) | Keep the row as a credit. Set `is_credit=true`. Net it into Scope 1. |
| 6 | fuel_deliveries | Seven exact duplicate rows | rejected (`duplicate_row`, 7 rows) | Keep the first copy. Reject each later identical copy. `INV-40967` and `INV-40729` share quantity and cost but differ in date and invoice number, so keep both. |
| 7 | fuel_deliveries | No delivery at all in 2025-11 | flagged (`missing_fuel_month`, 1 row) | Every other month in the window has one. Fuel was still burnt, so the invoice is missing from the export. Scope 1 for 2025-11 is a lower bound, not zero. |
| 8 | fuel_deliveries | A fuel type with no emission factor | rejected (`unknown_fuel_type`, 0 rows today) | Zero litres may not enter Scope 1 under a guessed factor. The guard rejects such a row and the API throws on the same condition. Today the file holds only Diesel and Petrol (ULP). |
| 9 | electricity | `MTR-07` collapses about 1000x from 2025-10 | flagged (`suspected_scale_error`, 9 rows) | Do not fabricate a corrected value for a compliance figure. Keep the raw reading. |
| 10 | electricity | All meters drop about 65% in 2026-03 | flagged (`expected_anomaly`, 1 row) | This is actually not an error. The detector finds the site-wide dip, then resolves it against the power incident `INC-2026-131` in the same month. |
| 11 | electricity | `MTR-06` absent from the sequence | flagged (`missing_meter_id`, 1 row) | Record the gap as an observation. |
| 12 | incidents | Two severity scales, numeric and text | fixed (`inconsistent_severity_scale`, 1 row) | Map both to one ordinal: 1=Low, 2=Medium, 3=High. Store `severity_norm` and `severity_rank`. |
| 13 | incidents | `INC-2025-011` reused for two incidents | flagged (`duplicate_incident_id`, 1 row) | The source id is not unique. Use a surrogate key. Keep both rows. |
| 14 | incidents | `DD/MM/YYYY` dates | fixed (not logged) | Parse day-first. |
| 15 | incidents | 30 of 42 descriptions are recycled text | flagged (`recycled_description`, 9 groups) | Nine descriptions repeat word for word across 30 incidents. The register is partly template text, so a count by description is not a count of distinct events. Keep every row and name the group. |
| 16 | incidents | One description, two severities | flagged (`inconsistent_severity_for_identical_text`, 3 groups) | Identical text is rated `2` in one row and `Low` in another. The recorded severity seems subjective, so a severity trend cannot be a reliable hazard trend. |
| 17 | suppliers | Near-duplicate entities | flagged (`duplicate_supplier`, 2 rows) | Group by a canonical name key or a shared ABN. Point each duplicate at the canonical row through `canonical_supplier_id`. Keep both rows. |
| 18 | suppliers | Malformed ABN `5501822` (7 digits) | flagged (`malformed_abn`, 1 row) | Keep the row. Flag the ABN because it is not 11 digits. |
| 19 | suppliers | Missing ABN (two suppliers) | flagged (`missing_abn`, 2 rows) | Keep the row. Flag the empty ABN. |
| 20 | suppliers | One entity, two categories | flagged (`supplier_category_conflict`, 1 row) | Ironline Fuel Distributors appears under `Fuel supply` and `Fuel`. The spend is split between the two categories. |

## Additional insight

The March 2026 substation outage moved emissions from Scope 2 to Scope 1.

Incident `INC-2026-131` records a regional substation failure on 2026-03-06.
Backup diesel generators ran for about three weeks. The data shows both halves:

- Grid electricity fell to 446,740 kg CO2e, about 64% below February.
- Diesel deliveries rose to 702,017 L, against a monthly average near 481,090 L.
  Seven deliveries drove the spike, five above 100,000 L.

The site did not cut its energy use. It shifted from grid power to on-site
diesel. Total emissions stayed high while the scope split changed. A report that
reads Scope 2 alone would show a false improvement.

The app derives this from the `expected_anomaly` row.

## AI use and workflow

I use the omp harness (https://github.com/can1357/oh-my-pi) for subscription-based Anthropic models and credit-based Kimi models. My general worker agent is opus-5 with opus-4-8 as fallback and kimi-k3 as advisor. The reason for using omp over Claude Code or Codex is for more efficient context management (specifically snapcompaction and hash-anchored edits that consume less tokens). Also, all models have their contexts limited to 200k through a models.yml file. I have found 1M context windows to be overkill and counter productive in many case, besides overly expensive. Too large a context means "dragging" the model to "places" in the latent space that are irrelevant, and so "distracting" the model and increasing the chance is goes off on a decoding path that is not what my task is. My workflow was to research the full-stack requirements and understand the codebase skeleton, then use /plan to implement each requirement step by step. Most importantly, the plan.md file included a requirement to pause after each new code file/change, have me review it manually, stage and commit it to the repo and proceed to the next step. Once I was happy with how the dashboard looked, I pushed all changes to the remote repo. Then, I set up the pipeline to host it in Railway. 

## What I chose to test and why

126 tests run without an API key.

| Suite | Tests | What it tests |
|---|---|---|
| `ingestion/tests/test_cleaning.py` | 28 | Each branch of the cleaning functions, run against the real messy values: four date formats, kL conversion, currency strings, two severity scales, the ATO checksum, and the supplier comparison key. |
| `ingestion/tests/test_quality.py` | 31 | Each detector runs on a small hand-built frame. A failure then names the detector, not the fixture. |
| `ingestion/tests/test_grounding.py` | 9 | The anti-hallucination guard. It needs no key and no database. |
| `api/tests/emissions.test.ts` | 13 | The emissions math: the kL anchor, the factor per fuel, month grouping, the credit row, and the two data-quality fields. |
| `api/tests/factors.test.ts` | 11 | Factor mapping and its failure modes. One case puts a factor row on the wrong unit. |
| `api/tests/routes.test.ts` | 19 | Caveat propagation, report completeness, and the feature-request route, all through the real routes. |
| `api/tests/exposure.test.ts` | 15 | The three estimators and their refusals: no reconciling multiplier, no clean baseline, no complete neighbour, and an explained anomaly held at zero. |

## What I would build next with another week

- A custom domain onbrand with esgagent.ai, move the codebase and pipeline to my VPS and deploy it from the server with nginx.
- An AI compliance summary and natural language query interface over the database.

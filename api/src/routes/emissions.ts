import { Router } from "express";
import { query } from "../db";
import {
  computeMonthly,
  type ElecRow,
  type FuelRow,
  type MonthlyEmissions,
} from "../emissions";
import { loadFactors } from "../factors";
import {
  buildExposure,
  estimateMissingMonth,
  estimateUnitScale,
  explainedAnomaly,
  type ExposureReport,
  type MeterReading,
} from "../exposure";

export const emissionsRouter = Router();

/**
 * A known limit of the emission figures, built from data_quality_issues.
 *
 * The server owns this text. The frontend showed a hardcoded sentence about
 * March 2026, which became wrong as soon as the data changed. A caveat is
 * present only while the issue that produced it is present.
 */
export interface Caveat {
  code: string;
  message: string;
  months: string[];
}

interface IssueRow {
  issue_type: string;
  record_ref: string | null;
  detail: string | null;
}

// The three issue types that limit an emission number. Order is fixed, so the
// response does not change shape between requests.
const CAVEAT_CODES = ["suspected_scale_error", "missing_fuel_month", "expected_anomaly"] as const;

async function monthly(): Promise<MonthlyEmissions[]> {
  const factors = await loadFactors();
  const fuel = await query<FuelRow>(
    "SELECT delivery_date::text AS delivery_date, fuel_type, quantity_l" +
      " FROM fuel_deliveries WHERE delivery_date IS NOT NULL",
  );
  const elec = await query<ElecRow>(
    "SELECT period::text AS period, consumption_kwh, is_flagged FROM electricity_readings",
  );
  return computeMonthly(fuel, elec, factors);
}

function sortedUnique(values: string[]): string[] {
  return [...new Set(values)].sort();
}

function buildMessage(code: string, rows: IssueRow[], months: string[]): string {
  if (code === "expected_anomaly") {
    // The detail already names the incident that explains the drop.
    return sortedUnique(rows.map((r) => r.detail ?? "")).join(" ");
  }
  if (code === "missing_fuel_month") {
    return (
      `No fuel delivery is recorded for ${months.join(", ")}.` +
      ` Scope 1 for ${months.length > 1 ? "these months is" : "that month is"}` +
      " a lower bound, not zero."
    );
  }
  const meters = sortedUnique(
    rows.map((r) => /^[A-Z]+-\d+/.exec(r.record_ref ?? "")?.[0] ?? "").filter(Boolean),
  );
  return (
    `Meter ${meters.join(", ")} reported values that the pipeline marked as` +
    ` unreliable in ${months.length} months. The readings are kept raw, so` +
    " Scope 2 for those months is a lower bound."
  );
}

async function caveats(): Promise<Caveat[]> {
  const rows = await query<IssueRow>(
    "SELECT issue_type, record_ref, detail FROM data_quality_issues" +
      " WHERE issue_type = ANY($1) ORDER BY issue_type, record_ref",
    [CAVEAT_CODES],
  );

  const out: Caveat[] = [];
  for (const code of CAVEAT_CODES) {
    const forCode = rows.filter((r) => r.issue_type === code);
    if (forCode.length === 0) continue;
    // Every record_ref of these three types carries the month it applies to.
    const months = sortedUnique(
      forCode.map((r) => /\d{4}-\d{2}/.exec(r.record_ref ?? "")?.[0] ?? "").filter(Boolean),
    );
    out.push({ code, message: buildMessage(code, forCode, months), months });
  }
  return out;
}

/**
 * The sized counterpart to caveats(): the same three issue types, priced.
 *
 * caveats() says a figure is a lower bound. This says by how much, so the
 * reader can rank the gaps instead of only knowing they exist. Both are built
 * from data_quality_issues, so a sized gap leaves the dashboard when the issue
 * behind it clears, and neither names a month, meter or incident in code.
 */
async function exposure(
  rows: MonthlyEmissions[],
  reportedKg: number,
): Promise<ExposureReport> {
  const [factors, readings, issues] = await Promise.all([
    loadFactors(),
    // Every reading of any meter that carries a flag. The unflagged months come
    // back too, because they are the baseline the estimator reconciles against.
    query<MeterReading>(
      "SELECT meter_id, period::text AS period, consumption_kwh, is_flagged" +
        " FROM electricity_readings WHERE meter_id IN" +
        " (SELECT meter_id FROM electricity_readings WHERE is_flagged)",
    ),
    query<IssueRow>(
      "SELECT issue_type, record_ref, detail FROM data_quality_issues" +
        " WHERE issue_type = ANY($1) ORDER BY issue_type, record_ref",
      [CAVEAT_CODES],
    ),
  ]);

  const monthsByCode = new Map<string, string[]>();
  for (const code of CAVEAT_CODES) {
    monthsByCode.set(
      code,
      sortedUnique(
        issues
          .filter((r) => r.issue_type === code)
          .map((r) => /\d{4}-\d{2}/.exec(r.record_ref ?? "")?.[0] ?? "")
          .filter(Boolean),
      ),
    );
  }

  const anomalies = issues.filter((r) => r.issue_type === "expected_anomaly");

  return buildExposure(
    [
      estimateUnitScale(readings, factors.electricity),
      estimateMissingMonth(rows, monthsByCode.get("missing_fuel_month") ?? []),
      anomalies.length > 0
        ? explainedAnomaly(
            monthsByCode.get("expected_anomaly") ?? [],
            sortedUnique(anomalies.map((r) => r.detail ?? "")).join(" "),
          )
        : null,
    ],
    reportedKg,
  );
}

emissionsRouter.get("/monthly", async (_req, res, next) => {
  try {
    res.json(await monthly());
  } catch (err) {
    next(err);
  }
});

emissionsRouter.get("/summary", async (_req, res, next) => {
  try {
    const [rows, notes] = await Promise.all([monthly(), caveats()]);
    let scope1 = 0;
    let scope2 = 0;
    for (const r of rows) {
      scope1 += r.scope1_kg;
      scope2 += r.scope2_kg;
    }
    const total = scope1 + scope2;
    res.json({
      months: rows.length,
      scope1_kg: Math.round(scope1),
      scope2_kg: Math.round(scope2),
      total_kg: Math.round(total),
      scope1_t: Math.round(scope1 / 10) / 100,
      scope2_t: Math.round(scope2 / 10) / 100,
      total_t: Math.round(total / 10) / 100,
      scope1_share: total > 0 ? Math.round((scope1 / total) * 1000) / 1000 : 0,
      scope2_share: total > 0 ? Math.round((scope2 / total) * 1000) / 1000 : 0,
      caveats: notes,
      exposure: await exposure(rows, total),
    });
  } catch (err) {
    next(err);
  }
});

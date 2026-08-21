// Sizing the gaps that data_quality_issues only names.
//
// The dashboard already says a figure is a lower bound. It never said by how
// much, and "lower bound" is not a number anybody can act on. A sustainability
// lead has to sign one figure and defend it, so she needs to know whether the
// understatement is a rounding error or a restatement.
//
// Two rules hold this file together.
//
// First, an estimate never replaces a reported figure. The conservative number
// stays exactly as computed. Everything here is a second, clearly labelled
// track. Quietly imputing a value is the same failure as the draft that
// returned 0 from the fuel-factor lookup: the number looks fine and nobody can
// see what was assumed.
//
// Second, an estimator must earn its estimate against the record's own
// history, and returns null when it cannot. That is the numeric counterpart of
// the substring grounding guard on the AI findings. No figure reaches the
// screen without a stated method and the evidence it rests on.
//
// There is no model call in this file. Every number below is arithmetic on
// rows, so the same data always produces the same answer.

import { monthKey, type MonthlyEmissions } from "./emissions";

export interface MeterReading {
  meter_id: string;
  period: string | Date;
  consumption_kwh: number | string;
  is_flagged: boolean;
}

/**
 * One sized gap.
 *
 * `kg_mid` is the figure to read. `kg_low` and `kg_high` are the band the
 * method can defend, never a decoration: a wide band is a weak estimate and
 * should look like one.
 */
export interface ExposureItem {
  /** The data_quality_issues type that produced this row. */
  code: string;
  /** Plain words for the client, not the issue type. */
  label: string;
  scope: 1 | 2 | null;
  /** The named estimator, so a reviewer can argue with the method itself. */
  method: string;
  /** How the estimate was reached, in words the client can check. */
  method_note: string;
  kg_low: number;
  kg_mid: number;
  kg_high: number;
  months: string[];
  /** The figures the estimate rests on. */
  evidence: string;
  /** The one document that closes this gap, or "" when nothing is missing. */
  action: string;
}

export interface ExposureReport {
  items: ExposureItem[];
  kg_low: number;
  /** Sum of kg_mid across items: the figure the client reads. */
  kg_mid: number;
  kg_high: number;
  /** kg_mid as a share of the reported total, rounded to 3 dp. */
  share_of_reported: number;
}

/** Candidate unit switches, largest first. kWh read as MWh is the ×1000 case. */
const SCALE_CANDIDATES = [1000, 100, 10];

/**
 * How far the restated readings may sit from the meter's own baseline and
 * still count as the same meter reporting in a different unit. A unit switch
 * reconciles tightly; 15% leaves room for real month-to-month movement without
 * accepting a multiplier that merely lands in the right postcode.
 */
const SCALE_TOLERANCE = 0.15;

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** One rounding convention for every figure this module publishes. */
function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/**
 * Size a flagged meter whose readings look like a unit switch.
 *
 * The test is the meter against itself: take the median of the months before
 * the flag, then the median of the flagged months multiplied by each candidate,
 * and keep the candidate that reconciles inside SCALE_TOLERANCE. Medians, not
 * means, because one genuine outlier inside the flagged run (a real outage
 * month) must not drag the comparison.
 *
 * Returns null when no candidate reconciles, or when the meter has no clean
 * baseline to reconcile against. A meter that reads low for a reason this
 * function cannot identify is left to the caveat that already describes it.
 * Guessing here would put an invented tonnage on a compliance screen.
 */
export function estimateUnitScale(
  readings: MeterReading[],
  electricityFactor: number,
): ExposureItem | null {
  const flagged = readings.filter((r) => r.is_flagged);
  const baseline = readings.filter((r) => !r.is_flagged);
  if (flagged.length === 0 || baseline.length === 0) return null;

  const baselineMedian = median(baseline.map((r) => Number(r.consumption_kwh)));
  const flaggedValues = flagged.map((r) => Number(r.consumption_kwh));
  const flaggedMedian = median(flaggedValues);
  if (baselineMedian <= 0 || flaggedMedian <= 0) return null;

  let best: { multiplier: number; gap: number } | null = null;
  for (const multiplier of SCALE_CANDIDATES) {
    const gap = Math.abs(flaggedMedian * multiplier - baselineMedian) / baselineMedian;
    if (gap <= SCALE_TOLERANCE && (best === null || gap < best.gap)) {
      best = { multiplier, gap };
    }
  }
  if (best === null) return null;

  const rawKwh = flaggedValues.reduce((a, b) => a + b, 0);
  // The readings are already counted at face value in the reported figure, so
  // the gap is the restated total less what is on the books, not the total.
  const mid = (rawKwh * best.multiplier - rawKwh) * electricityFactor;
  // The residual uncertainty is the reconciliation gap itself: the multiplier
  // is a discrete choice, and how well it reconciles is what remains unknown.
  const spread = mid * best.gap;

  const meters = [...new Set(flagged.map((r) => r.meter_id))].sort();
  const months = [...new Set(flagged.map((r) => monthKey(r.period)))].sort();
  const times = best.multiplier.toLocaleString("en-AU");

  return {
    code: "suspected_scale_error",
    label: `Meter ${meters.join(", ")} reports in the wrong unit`,
    scope: 2,
    method: "unit_scale_ratio",
    method_note:
      `Multiply the flagged readings by ${times}. They then agree with the` +
      ` earlier months from the same meter. The meter reports in a larger unit.` +
      ` The new figure is the reading of the meter in the correct unit.`,
    kg_low: round2(mid - spread),
    kg_mid: round2(mid),
    kg_high: round2(mid + spread),
    months,
    evidence:
      `The ${baseline.length} months before the flag have a median of` +
      ` ${Math.round(baselineMedian).toLocaleString("en-AU")} kWh.` +
      ` The ${flagged.length} flagged months, at ×${times}, have a median of` +
      ` ${Math.round(flaggedMedian * best.multiplier).toLocaleString("en-AU")} kWh.` +
      ` The two values agree within ${Math.round(best.gap * 1000) / 10}%.`,
    action:
      `Request the ${meters.join(", ")} calibration record for ${months[0]} to` +
      ` ${months[months.length - 1]}. Confirm the unit of the meter.`,
  };
}

/**
 * Size a month that holds no fuel invoice at all.
 *
 * The estimate interpolates the neighbouring months' Scope 1 kg directly rather
 * than their litres, so it carries no assumption about which fuel the missing
 * invoice was for. A neighbour that is itself incomplete is not evidence and is
 * skipped.
 *
 * The band is the two neighbours: a month between them is a fair reading of
 * this site's run rate, and claiming tighter than that would be false
 * precision. Returns null when no complete neighbour exists.
 */
export function estimateMissingMonth(
  monthly: MonthlyEmissions[],
  missingMonths: string[],
): ExposureItem | null {
  const completeScope1 = new Map(
    monthly.filter((r) => r.scope1_has_deliveries).map((r) => [r.month, r.scope1_kg]),
  );
  const order = monthly.map((r) => r.month);

  let low = 0;
  let mid = 0;
  let high = 0;
  const sized: string[] = [];
  const evidence: string[] = [];

  for (const month of missingMonths) {
    const at = order.indexOf(month);
    if (at === -1) continue;

    const neighbours: { month: string; kg: number }[] = [];
    for (const beside of [at - 1, at + 1]) {
      const m = order[beside];
      const kg = m === undefined ? undefined : completeScope1.get(m);
      if (kg !== undefined) neighbours.push({ month: m, kg });
    }
    if (neighbours.length === 0) continue;

    const values = neighbours.map((n) => n.kg);
    low += Math.min(...values);
    high += Math.max(...values);
    mid += values.reduce((a, b) => a + b, 0) / values.length;
    sized.push(month);
    evidence.push(
      neighbours
        .map((n) => `${n.month} ${Math.round(n.kg / 1000).toLocaleString("en-AU")} t`)
        .join(" and "),
    );
  }

  if (sized.length === 0) return null;

  return {
    code: "missing_fuel_month",
    label: `No fuel invoice for ${sized.join(", ")}`,
    scope: 1,
    method: "neighbour_interpolation",
    method_note:
      "This figure comes from the Scope 1 total of the month before and the" +
      " month after. It assumes no fuel type. The reported total still counts" +
      " this month as zero.",
    kg_low: round2(low),
    kg_mid: round2(mid),
    kg_high: round2(high),
    months: sized,
    evidence: `Complete months beside the gap: ${evidence.join("; ")}.`,
    action: `Request the ${sized.join(", ")} fuel delivery invoices from the supplier.`,
  };
}

/**
 * A flagged month that is not a gap at all.
 *
 * The pipeline explains this drop with a named incident, so the reading is
 * right and there is nothing to restate. It is on the list on purpose: a panel
 * that sized every flag equally would be imputing, and the client would have no
 * way to tell a data fault from a real event. Zero here is a finding.
 */
export function explainedAnomaly(months: string[], detail: string): ExposureItem {
  return {
    code: "expected_anomaly",
    label: "An incident explains this electricity drop",
    scope: null,
    method: "explained",
    method_note:
      "The pipeline links this drop to an incident in the register. The reading" +
      " is correct.",
    kg_low: 0,
    kg_mid: 0,
    kg_high: 0,
    months: [...months].sort(),
    evidence: detail,
    action: "",
  };
}

/**
 * Assemble the sized items, ordered by the figure the client should act on
 * first. Explained rows carry zero and therefore sort last on their own.
 */
export function buildExposure(
  items: (ExposureItem | null)[],
  reportedKg: number,
): ExposureReport {
  const kept = items.filter((i): i is ExposureItem => i !== null);
  kept.sort((a, b) => b.kg_mid - a.kg_mid);

  const low = kept.reduce((a, i) => a + i.kg_low, 0);
  const mid = kept.reduce((a, i) => a + i.kg_mid, 0);
  const high = kept.reduce((a, i) => a + i.kg_high, 0);

  return {
    items: kept,
    kg_low: round2(low),
    kg_mid: round2(mid),
    kg_high: round2(high),
    share_of_reported: reportedKg > 0 ? Math.round((mid / reportedKg) * 1000) / 1000 : 0,
  };
}

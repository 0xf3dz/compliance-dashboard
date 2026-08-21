import { describe, expect, it } from "vitest";
import type { MonthlyEmissions } from "../src/emissions";
import {
  buildExposure,
  estimateMissingMonth,
  estimateUnitScale,
  explainedAnomaly,
  type MeterReading,
} from "../src/exposure";

// The Scope 2 factor from emission_factors.csv, stated per call. Nothing here
// falls back to a default, for the same reason the emissions tests do not.
const ELECTRICITY = 0.71;

function reading(month: string, kwh: number, flagged: boolean): MeterReading {
  return {
    meter_id: "MTR-07",
    period: `${month}-01`,
    consumption_kwh: kwh,
    is_flagged: flagged,
  };
}

// MTR-07 as the pipeline loaded it: nine clean months near 250,000 kWh, then
// nine flagged months near 250. The run includes 2026-03 at 85.1, the real
// outage month, which is why the estimator compares medians and not means.
const MTR07: MeterReading[] = [
  reading("2025-01", 252822.7, false),
  reading("2025-02", 243870.8, false),
  reading("2025-03", 260257.3, false),
  reading("2025-04", 271277.3, false),
  reading("2025-05", 245567.5, false),
  reading("2025-06", 250817.9, false),
  reading("2025-07", 252097.3, false),
  reading("2025-08", 244199.2, false),
  reading("2025-09", 274790.9, false),
  reading("2025-10", 277, true),
  reading("2025-11", 255.5, true),
  reading("2025-12", 266.7, true),
  reading("2026-01", 248.4, true),
  reading("2026-02", 244.6, true),
  reading("2026-03", 85.1, true),
  reading("2026-04", 272.1, true),
  reading("2026-05", 266.4, true),
  reading("2026-06", 261.2, true),
];

describe("estimateUnitScale", () => {
  it("prices the MTR-07 unit switch against the meter's own history", () => {
    const item = estimateUnitScale(MTR07, ELECTRICITY);
    expect(item).not.toBeNull();
    // Flagged kWh sum 2177.0. Restated at x1000 that is 2,177,000 kWh, and
    // 2177 of it is already counted, so the gap is 2,174,823 kWh.
    // 2,174,823 * 0.71 = 1,544,124.33 kg.
    expect(item?.kg_mid).toBe(1544124.33);
    expect(item?.scope).toBe(2);
    expect(item?.method).toBe("unit_scale_ratio");
    expect(item?.months).toHaveLength(9);
  });

  it("brackets the estimate by how well the multiplier reconciles", () => {
    const item = estimateUnitScale(MTR07, ELECTRICITY);
    // The band is the reconciliation gap, so it must straddle the midpoint and
    // stay narrow: a proven unit switch is not a wide guess.
    expect(item!.kg_low).toBeLessThan(item!.kg_mid);
    expect(item!.kg_high).toBeGreaterThan(item!.kg_mid);
    expect((item!.kg_high - item!.kg_low) / item!.kg_mid).toBeLessThan(0.2);
  });

  it("names the evidence and the document that closes the gap", () => {
    const item = estimateUnitScale(MTR07, ELECTRICITY);
    // A figure with no checkable basis is the failure mode this feature exists
    // to avoid, so both fields must carry content.
    expect(item?.evidence).toContain("1,000");
    expect(item?.action).toContain("MTR-07");
    expect(item?.action).toContain("2025-10");
  });

  it("refuses to estimate when no multiplier reconciles", () => {
    // A meter that halved for an unknown reason. No unit switch explains it, so
    // the estimator must decline rather than invent a tonnage.
    const halved: MeterReading[] = [
      reading("2025-01", 100000, false),
      reading("2025-02", 100000, false),
      reading("2025-03", 50000, true),
      reading("2025-04", 50000, true),
    ];
    expect(estimateUnitScale(halved, ELECTRICITY)).toBeNull();
  });

  it("refuses to estimate without a clean baseline to reconcile against", () => {
    const allFlagged = MTR07.map((r) => ({ ...r, is_flagged: true }));
    expect(estimateUnitScale(allFlagged, ELECTRICITY)).toBeNull();
    const noneFlagged = MTR07.map((r) => ({ ...r, is_flagged: false }));
    expect(estimateUnitScale(noneFlagged, ELECTRICITY)).toBeNull();
  });

  it("scales the estimate with the factor it is given", () => {
    const item = estimateUnitScale(MTR07, ELECTRICITY * 2);
    expect(item?.kg_mid).toBe(1544124.33 * 2);
  });
});

function month(m: string, scope1: number, hasDeliveries = true): MonthlyEmissions {
  return {
    month: m,
    scope1_kg: scope1,
    scope2_kg: 0,
    total_kg: scope1,
    scope2_flagged_kwh: 0,
    scope1_has_deliveries: hasDeliveries,
  };
}

describe("estimateMissingMonth", () => {
  it("interpolates the missing month from the months either side", () => {
    const rows = [
      month("2025-10", 1_271_824),
      month("2025-11", 0, false),
      month("2025-12", 1_317_306),
    ];
    const item = estimateMissingMonth(rows, ["2025-11"]);
    expect(item?.kg_mid).toBe((1_271_824 + 1_317_306) / 2);
    expect(item?.kg_low).toBe(1_271_824);
    expect(item?.kg_high).toBe(1_317_306);
    expect(item?.scope).toBe(1);
    expect(item?.months).toEqual(["2025-11"]);
  });

  it("ignores a neighbour that is itself incomplete", () => {
    // 2025-10 holds no invoice either, so only 2025-12 is evidence and the
    // band collapses onto that single month.
    const rows = [
      month("2025-10", 0, false),
      month("2025-11", 0, false),
      month("2025-12", 1_317_306),
    ];
    const item = estimateMissingMonth(rows, ["2025-11"]);
    expect(item?.kg_mid).toBe(1_317_306);
    expect(item?.kg_low).toBe(1_317_306);
    expect(item?.kg_high).toBe(1_317_306);
  });

  it("refuses to estimate when no complete neighbour exists", () => {
    const rows = [month("2025-10", 0, false), month("2025-11", 0, false)];
    expect(estimateMissingMonth(rows, ["2025-11"])).toBeNull();
  });

  it("returns null when the reported months hold no gap", () => {
    const rows = [month("2025-10", 100), month("2025-11", 200)];
    expect(estimateMissingMonth(rows, [])).toBeNull();
  });

  it("sizes more than one missing month", () => {
    const rows = [
      month("2025-01", 1000),
      month("2025-02", 0, false),
      month("2025-03", 3000),
      month("2025-04", 0, false),
      month("2025-05", 5000),
    ];
    const item = estimateMissingMonth(rows, ["2025-02", "2025-04"]);
    // (1000+3000)/2 + (3000+5000)/2 = 2000 + 4000.
    expect(item?.kg_mid).toBe(6000);
    expect(item?.months).toEqual(["2025-02", "2025-04"]);
  });
});

describe("explainedAnomaly", () => {
  it("prices an explained movement at zero and says so", () => {
    const item = explainedAnomaly(["2026-03"], "Explained by incident INC-2026-131.");
    // The whole point of the row: a flag the client should not act on. If this
    // ever returns a figure, the panel has started imputing real events.
    expect(item.kg_mid).toBe(0);
    expect(item.kg_low).toBe(0);
    expect(item.kg_high).toBe(0);
    expect(item.method).toBe("explained");
    expect(item.action).toBe("");
    expect(item.evidence).toContain("INC-2026-131");
  });
});

describe("buildExposure", () => {
  it("totals the sized gaps and ranks the largest first", () => {
    const report = buildExposure(
      [
        estimateMissingMonth(
          [month("2025-10", 1_271_824), month("2025-11", 0, false), month("2025-12", 1_317_306)],
          ["2025-11"],
        ),
        estimateUnitScale(MTR07, ELECTRICITY),
        explainedAnomaly(["2026-03"], "Explained by incident INC-2026-131."),
      ],
      45_385_710,
    );
    expect(report.items).toHaveLength(3);
    expect(report.items.map((i) => i.method)).toEqual([
      "unit_scale_ratio",
      "neighbour_interpolation",
      "explained",
    ]);
    // 1,544,124.33 + 1,294,565 = 2,838,689.33 kg against a reported
    // 45,385,710 kg, which is 6.3% of the figure on the dashboard.
    expect(report.kg_mid).toBe(2838689.33);
    expect(report.share_of_reported).toBe(0.063);
  });

  it("drops the estimators that declined", () => {
    const report = buildExposure([null, null, explainedAnomaly(["2026-03"], "x")], 1000);
    expect(report.items).toHaveLength(1);
    expect(report.kg_mid).toBe(0);
    expect(report.share_of_reported).toBe(0);
  });

  it("reports no share when there is no reported figure to compare against", () => {
    const report = buildExposure([estimateUnitScale(MTR07, ELECTRICITY)], 0);
    expect(report.kg_mid).toBe(1544124.33);
    expect(report.share_of_reported).toBe(0);
  });
});

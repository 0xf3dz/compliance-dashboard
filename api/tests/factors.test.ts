import { describe, expect, it } from "vitest";
import { mapFactorRows, type FactorRow } from "../src/factors";

// The three rows of emission_factors.csv, with the numbers as strings because
// that is what pg returns for a numeric column.
const REAL: FactorRow[] = [
  {
    activity: "Diesel combustion (stationary & transport)",
    scope: 1,
    unit: "L",
    kg_co2e_per_unit: "2.70",
  },
  { activity: "Petrol (ULP) combustion", scope: 1, unit: "L", kg_co2e_per_unit: "2.31" },
  { activity: "Grid electricity - Queensland", scope: 2, unit: "kWh", kg_co2e_per_unit: "0.71" },
];

describe("mapFactorRows", () => {
  it("maps the three real rows", () => {
    expect(mapFactorRows(REAL)).toEqual({
      diesel: 2.7,
      petrol: 2.31,
      electricity: 0.71,
    });
  });

  it("coerces a numeric string to a number", () => {
    const factors = mapFactorRows(REAL);
    expect(typeof factors.diesel).toBe("number");
    expect(factors.diesel).toBe(2.7);
  });

  it("accepts a number as well as a string", () => {
    const rows = REAL.map((r) => ({ ...r, kg_co2e_per_unit: Number(r.kg_co2e_per_unit) }));
    expect(mapFactorRows(rows)).toEqual({ diesel: 2.7, petrol: 2.31, electricity: 0.71 });
  });

  it("matches the activity without regard to case", () => {
    const rows = REAL.map((r) => ({ ...r, activity: r.activity.toUpperCase() }));
    expect(mapFactorRows(rows).diesel).toBe(2.7);
  });

  it("keeps diesel and petrol apart although both are Scope 1 litres", () => {
    const factors = mapFactorRows(REAL);
    expect(factors.diesel).not.toBe(factors.petrol);
  });
});

describe("mapFactorRows failure modes", () => {
  it("throws when the electricity row is absent", () => {
    expect(() => mapFactorRows(REAL.slice(0, 2))).toThrow(
      "missing emission factor: electricity",
    );
  });

  it("throws when the diesel row is absent", () => {
    expect(() => mapFactorRows([REAL[1], REAL[2]])).toThrow("missing emission factor: diesel");
  });

  it("throws on two diesel rows", () => {
    const rows: FactorRow[] = [
      ...REAL,
      { activity: "Diesel backup generator", scope: 1, unit: "L", kg_co2e_per_unit: "2.68" },
    ];
    expect(() => mapFactorRows(rows)).toThrow("ambiguous emission factor: diesel");
  });

  it("does not match a diesel row on the wrong unit", () => {
    // A row in kL is a different quantity. Reading it as litres understates
    // Scope 1 by a factor of 1000, so the row must not match at all.
    const rows: FactorRow[] = [{ ...REAL[0], unit: "kL" }, REAL[1], REAL[2]];
    expect(() => mapFactorRows(rows)).toThrow("missing emission factor: diesel");
  });

  it("does not match an electricity row on the wrong scope", () => {
    const rows: FactorRow[] = [REAL[0], REAL[1], { ...REAL[2], scope: 1 }];
    expect(() => mapFactorRows(rows)).toThrow("missing emission factor: electricity");
  });

  it("throws on an empty table rather than return a default", () => {
    expect(() => mapFactorRows([])).toThrow("missing emission factor: diesel");
  });
});

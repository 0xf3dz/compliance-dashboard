import { describe, expect, it } from "vitest";
import {
  computeMonthly,
  fuelFactor,
  type ElecRow,
  type Factors,
  type FuelRow,
} from "../src/emissions";

// The three rows of emission_factors.csv. Every call states its factors, so a
// test cannot pass because of a default that the API no longer uses.
const FACTORS: Factors = { diesel: 2.7, petrol: 2.31, electricity: 0.71 };

describe("computeMonthly", () => {
  it("converts the kL anchor row (INV-40373) to the right Scope 1 kg", () => {
    // 84.03 kL -> 84030 L (done in ingestion) -> 84030 * 2.70 = 226881 kg.
    const fuel: FuelRow[] = [
      { delivery_date: "2025-05-10", fuel_type: "Diesel", quantity_l: 84030 },
    ];
    const [row] = computeMonthly(fuel, [], FACTORS);
    expect(row.month).toBe("2025-05");
    expect(row.scope1_kg).toBe(226881);
    expect(row.scope2_kg).toBe(0);
    expect(row.total_kg).toBe(226881);
  });

  it("selects the factor per fuel type", () => {
    const fuel: FuelRow[] = [
      { delivery_date: "2025-01-15", fuel_type: "Diesel", quantity_l: 1000 },
      { delivery_date: "2025-01-15", fuel_type: "Petrol (ULP)", quantity_l: 1000 },
    ];
    const [row] = computeMonthly(fuel, [], FACTORS);
    // 1000*2.70 + 1000*2.31 = 2700 + 2310 = 5010.
    expect(row.scope1_kg).toBe(5010);
  });

  it("groups Scope 1 by delivery month and Scope 2 by period month", () => {
    const fuel: FuelRow[] = [
      { delivery_date: "2025-01-31", fuel_type: "Diesel", quantity_l: 100 },
      { delivery_date: "2025-02-01", fuel_type: "Diesel", quantity_l: 200 },
    ];
    const elec: ElecRow[] = [
      { period: "2025-01-01", consumption_kwh: 1000, is_flagged: false },
      { period: "2025-02-01", consumption_kwh: 2000, is_flagged: false },
    ];
    const rows = computeMonthly(fuel, elec, FACTORS);
    expect(rows.map((r) => r.month)).toEqual(["2025-01", "2025-02"]);
    expect(rows[0].scope1_kg).toBe(270); // 100*2.70
    expect(rows[0].scope2_kg).toBe(710); // 1000*0.71
    expect(rows[1].scope1_kg).toBe(540); // 200*2.70
    expect(rows[1].scope2_kg).toBe(1420); // 2000*0.71
  });

  it("nets a fuel credit (negative quantity) out of Scope 1", () => {
    // INV-41777: -12500 L diesel credit reduces the month's Scope 1.
    const fuel: FuelRow[] = [
      { delivery_date: "2025-08-14", fuel_type: "Diesel", quantity_l: 36216 },
      { delivery_date: "2025-08-14", fuel_type: "Diesel", quantity_l: -12500 },
    ];
    const [row] = computeMonthly(fuel, [], FACTORS);
    // (36216 - 12500) * 2.70 = 23716 * 2.70 = 64033.2
    expect(row.scope1_kg).toBe(64033.2);
  });

  it("uses the factors it is given, not a built-in constant", () => {
    const fuel: FuelRow[] = [
      { delivery_date: "2025-01-15", fuel_type: "Diesel", quantity_l: 1000 },
    ];
    const [real] = computeMonthly(fuel, [], FACTORS);
    const [changed] = computeMonthly(fuel, [], { ...FACTORS, diesel: 1 });
    expect(real.scope1_kg).toBe(2700);
    expect(changed.scope1_kg).toBe(1000);
  });
});

describe("fuelFactor", () => {
  it("throws on an unknown fuel type", () => {
    expect(() => fuelFactor("LPG", FACTORS)).toThrow("unknown fuel type: LPG");
  });

  it("makes computeMonthly fail rather than emit zero kg", () => {
    // The draft returned 0 here, so the litres stayed in the database and
    // contributed nothing to Scope 1, with nothing on screen to show it.
    const fuel: FuelRow[] = [
      { delivery_date: "2025-01-15", fuel_type: "LPG", quantity_l: 10000 },
    ];
    expect(() => computeMonthly(fuel, [], FACTORS)).toThrow("unknown fuel type: LPG");
  });

  it("matches the two real fuel types", () => {
    expect(fuelFactor("Diesel", FACTORS)).toBe(2.7);
    expect(fuelFactor("Petrol (ULP)", FACTORS)).toBe(2.31);
  });
});

describe("scope2_flagged_kwh", () => {
  it("sums the flagged rows only", () => {
    // The MTR-07 collapse: 250 kWh from a flagged meter beside a healthy one.
    const elec: ElecRow[] = [
      { period: "2025-10-01", consumption_kwh: 250, is_flagged: true },
      { period: "2025-10-01", consumption_kwh: 300, is_flagged: true },
      { period: "2025-10-01", consumption_kwh: 1000, is_flagged: false },
    ];
    const [row] = computeMonthly([], elec, FACTORS);
    expect(row.scope2_flagged_kwh).toBe(550);
    expect(row.scope2_kg).toBe(1100.5); // 1550 kWh * 0.71
  });

  it("is zero when no reading is flagged", () => {
    const elec: ElecRow[] = [
      { period: "2025-10-01", consumption_kwh: 1000, is_flagged: false },
    ];
    expect(computeMonthly([], elec, FACTORS)[0].scope2_flagged_kwh).toBe(0);
  });
});

describe("scope1_has_deliveries", () => {
  it("is false for a month with electricity and no fuel", () => {
    // 2025-11 in the real data. Its Scope 1 is a lower bound, not zero.
    const elec: ElecRow[] = [
      { period: "2025-11-01", consumption_kwh: 1000, is_flagged: false },
    ];
    const [row] = computeMonthly([], elec, FACTORS);
    expect(row.scope1_has_deliveries).toBe(false);
    expect(row.scope1_kg).toBe(0);
  });

  it("is true for a month that has a fuel row", () => {
    const fuel: FuelRow[] = [
      { delivery_date: "2025-12-19", fuel_type: "Diesel", quantity_l: 96595 },
    ];
    expect(computeMonthly(fuel, [], FACTORS)[0].scope1_has_deliveries).toBe(true);
  });

  it("marks only the month that lacks fuel", () => {
    const fuel: FuelRow[] = [
      { delivery_date: "2025-10-05", fuel_type: "Diesel", quantity_l: 100 },
      { delivery_date: "2025-12-05", fuel_type: "Diesel", quantity_l: 100 },
    ];
    const elec: ElecRow[] = [
      { period: "2025-10-01", consumption_kwh: 1000, is_flagged: false },
      { period: "2025-11-01", consumption_kwh: 1000, is_flagged: false },
      { period: "2025-12-01", consumption_kwh: 1000, is_flagged: false },
    ];
    const rows = computeMonthly(fuel, elec, FACTORS);
    expect(rows.map((r) => r.scope1_has_deliveries)).toEqual([true, false, true]);
  });
});

// Pure emissions math. No database, no express: this is the unit-tested core.
//
// Factors are injected, never defaulted. They live in the emission_factors
// table, which the pipeline loads from emission_factors.csv, and factors.ts
// reads them from there. A constant here would be a second source of truth
// that silently outlives an edit to the data.

export interface FuelRow {
  delivery_date: string | Date;
  fuel_type: string;
  quantity_l: number | string;
}

export interface ElecRow {
  period: string | Date;
  consumption_kwh: number | string;
  is_flagged: boolean;
}

export interface Factors {
  diesel: number; // kg CO2e per litre
  petrol: number; // kg CO2e per litre
  electricity: number; // kg CO2e per kWh
}

export interface MonthlyEmissions {
  month: string; // YYYY-MM
  scope1_kg: number;
  scope2_kg: number;
  total_kg: number;
  /** kWh from readings flagged as unreliable, included in scope2_kg above. */
  scope2_flagged_kwh: number;
  /** False when the month holds no fuel row at all, so scope1_kg is a floor. */
  scope1_has_deliveries: boolean;
}

/** "2025-05-10" or a Date -> "2025-05". Exported so exposure.ts cannot grow a
   second, subtly different month formatter. */
export function monthKey(d: string | Date): string {
  if (d instanceof Date) {
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, "0");
    return `${y}-${m}`;
  }
  return String(d).slice(0, 7); // "2025-05-10" -> "2025-05"
}

/**
 * Map a raw fuel type to its factor. Throws on an unknown type.
 *
 * The draft returned 0 here, which turned an unmapped fuel into a silent
 * discard: the litres stayed in the database and contributed nothing to
 * Scope 1. ingestion/quality.py rejects such a row at load time with the same
 * substring rule in the same order, so this throw should be unreachable. It
 * exists so that if the two ever disagree, the API fails loudly.
 */
export function fuelFactor(fuelType: string, factors: Factors): number {
  const t = fuelType.toLowerCase();
  if (t.includes("petrol")) return factors.petrol;
  if (t.includes("diesel")) return factors.diesel;
  throw new Error(`unknown fuel type: ${fuelType}`);
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/**
 * Aggregate cleaned fuel and electricity rows into monthly Scope 1 / Scope 2
 * emissions. Fuel credits (negative quantity_l) subtract, as required by net
 * Scope 1. Returns one row per month that has any activity, sorted ascending.
 *
 * Two fields carry data quality to the consumer instead of hiding it:
 * scope2_flagged_kwh reports how much of the month's electricity came from a
 * flagged meter, and scope1_has_deliveries marks a month with no fuel invoice
 * so its Scope 1 reads as a lower bound rather than a true zero.
 */
export function computeMonthly(
  fuelRows: FuelRow[],
  elecRows: ElecRow[],
  factors: Factors,
): MonthlyEmissions[] {
  const scope1 = new Map<string, number>();
  const scope2 = new Map<string, number>();
  const flaggedKwh = new Map<string, number>();
  const fuelMonths = new Set<string>();

  for (const r of fuelRows) {
    const key = monthKey(r.delivery_date);
    const litres = Number(r.quantity_l);
    scope1.set(key, (scope1.get(key) ?? 0) + litres * fuelFactor(r.fuel_type, factors));
    fuelMonths.add(key);
  }

  for (const r of elecRows) {
    const key = monthKey(r.period);
    const kwh = Number(r.consumption_kwh);
    scope2.set(key, (scope2.get(key) ?? 0) + kwh * factors.electricity);
    if (r.is_flagged) {
      flaggedKwh.set(key, (flaggedKwh.get(key) ?? 0) + kwh);
    }
  }

  const months = new Set<string>([...scope1.keys(), ...scope2.keys()]);
  return [...months].sort().map((month) => {
    const s1 = round2(scope1.get(month) ?? 0);
    const s2 = round2(scope2.get(month) ?? 0);
    return {
      month,
      scope1_kg: s1,
      scope2_kg: s2,
      total_kg: round2(s1 + s2),
      scope2_flagged_kwh: round2(flaggedKwh.get(month) ?? 0),
      scope1_has_deliveries: fuelMonths.has(month),
    };
  });
}

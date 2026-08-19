// Emission factors, read from the database that the pipeline loaded from
// emission_factors.csv. There is no constant here and no default anywhere. A
// hardcoded factor keeps its old value after somebody edits the CSV, and the
// dashboard then reports a number that no source file supports.

import { query } from "./db";
import type { Factors } from "./emissions";

export interface FactorRow {
  activity: string;
  scope: number;
  unit: string;
  kg_co2e_per_unit: number | string;
}

interface FactorRule {
  scope: number;
  unit: string;
  /** Lowercase substring the activity must contain. Empty matches any activity. */
  activity: string;
}

// One rule per key of Factors. The scope and unit pair is the real identity of
// a factor. The activity substring separates the two Scope 1 litre rows.
const RULES: Record<keyof Factors, FactorRule> = {
  diesel: { scope: 1, unit: "L", activity: "diesel" },
  petrol: { scope: 1, unit: "L", activity: "petrol" },
  electricity: { scope: 2, unit: "kWh", activity: "" },
};

/**
 * Find the one row for a key.
 *
 * Throws when no row matches, and when more than one matches. Both cases are
 * unrecoverable: the alternative is to guess a factor, and every emission
 * figure on the dashboard then rests on that guess.
 */
function factorFor(rows: FactorRow[], key: keyof Factors): number {
  const rule = RULES[key];
  const matches = rows.filter(
    (r) =>
      Number(r.scope) === rule.scope &&
      r.unit === rule.unit &&
      r.activity.toLowerCase().includes(rule.activity),
  );
  if (matches.length === 0) {
    throw new Error(`missing emission factor: ${key}`);
  }
  if (matches.length > 1) {
    throw new Error(`ambiguous emission factor: ${key}`);
  }
  return Number(matches[0].kg_co2e_per_unit);
}

/** Map the emission_factors rows onto the three keys the math needs. */
export function mapFactorRows(rows: FactorRow[]): Factors {
  return {
    diesel: factorFor(rows, "diesel"),
    petrol: factorFor(rows, "petrol"),
    electricity: factorFor(rows, "electricity"),
  };
}

/** Read the factors from Postgres. Every request gets the current values. */
export async function loadFactors(): Promise<Factors> {
  const rows = await query<FactorRow>(
    "SELECT activity, scope, unit, kg_co2e_per_unit FROM emission_factors",
  );
  return mapFactorRows(rows);
}

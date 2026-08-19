// Typed fetch wrappers to the Ironbark API.

const BASE = "http://localhost:3000";

export interface MonthlyEmissions {
  month: string;
  scope1_kg: number;
  scope2_kg: number;
  total_kg: number;
  /** kWh from readings the pipeline flagged, already inside scope2_kg. */
  scope2_flagged_kwh: number;
  /** False when the month holds no fuel invoice, so scope1_kg is a floor. */
  scope1_has_deliveries: boolean;
}

/** A known limit of the emission figures. The server owns this text. */
export interface Caveat {
  code: string;
  message: string;
  months: string[];
}

export interface EmissionsSummary {
  months: number;
  scope1_kg: number;
  scope2_kg: number;
  total_kg: number;
  scope1_t: number;
  scope2_t: number;
  total_t: number;
  scope1_share: number;
  scope2_share: number;
  caveats: Caveat[];
}

export interface Count {
  key: string;
  count: number;
}

export interface Trend {
  month: string;
  count: number;
}

export interface IncidentSummary {
  total: number;
  trend: Trend[];
  by_type: Count[];
  by_severity: Count[];
}

export interface AiFinding {
  id: number;
  incident_id: string;
  ai_category: string;
  is_psychosocial: boolean;
  severity_mismatch: boolean;
  mismatch_detail: string | null;
  evidence_quote: string;
  rationale: string;
  model: string;
  prompt_version: string;
  /** True when the incident text changed after Claude read it. */
  stale: boolean;
  description: string;
  type_code: string;
  severity_norm: string;
  incident_date: string;
  location: string;
}

export interface IssueItem {
  id: number;
  source_file: string;
  source_row: number | null;
  record_ref: string | null;
  issue_type: string;
  field: string | null;
  raw_value: string | null;
  action: "fixed" | "flagged" | "rejected";
  resolution: string | null;
  detail: string | null;
}

export interface IssueTypeGroup {
  issue_type: string;
  action: string;
  count: number;
  items: IssueItem[];
}

export interface FileGroup {
  source_file: string;
  count: number;
  issue_types: IssueTypeGroup[];
}

export interface DataQualityReport {
  total: number;
  files: FileGroup[];
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${path} failed: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  emissionsMonthly: () => getJson<MonthlyEmissions[]>("/api/emissions/monthly"),
  emissionsSummary: () => getJson<EmissionsSummary>("/api/emissions/summary"),
  incidentSummary: () => getJson<IncidentSummary>("/api/incidents/summary"),
  aiFindings: () => getJson<AiFinding[]>("/api/incidents/ai-findings"),
  dataQuality: () => getJson<DataQualityReport>("/api/data-quality"),
};

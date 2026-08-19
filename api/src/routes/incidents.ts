import { Router } from "express";
import { query } from "../db";

export const incidentsRouter = Router();

interface CountRow {
  key: string;
  count: number;
}

interface TrendRow {
  month: string;
  count: number;
}

incidentsRouter.get("/summary", async (_req, res, next) => {
  try {
    const byMonth = await query<TrendRow>(
      "SELECT to_char(incident_date, 'YYYY-MM') AS month, count(*)::int AS count" +
        " FROM incidents WHERE incident_date IS NOT NULL" +
        " GROUP BY 1 ORDER BY 1",
    );
    const byType = await query<CountRow>(
      "SELECT type_code AS key, count(*)::int AS count FROM incidents" +
        " GROUP BY 1 ORDER BY count DESC, key",
    );
    const bySeverity = await query<CountRow>(
      "SELECT severity_norm AS key, count(*)::int AS count FROM incidents" +
        " GROUP BY 1, severity_rank ORDER BY severity_rank",
    );
    res.json({
      total: byType.reduce((s, r) => s + r.count, 0),
      trend: byMonth,
      by_type: byType,
      by_severity: bySeverity,
    });
  } catch (err) {
    next(err);
  }
});

incidentsRouter.get("/ai-findings", async (_req, res, next) => {
  try {
    // The join key is source_row, the CSV data-row number. The pipeline drops
    // and recreates incidents on every run, so the surrogate id changes and a
    // foreign key to it cannot survive. source_row is a property of the file.
    //
    // stale compares the digest taken when Claude read the description against
    // the digest of the description now in the database. A true value means the
    // text changed after classification, so the finding describes older words.
    const rows = await query(
      "SELECT f.id, f.incident_id, f.ai_category, f.is_psychosocial," +
        " f.severity_mismatch, f.mismatch_detail, f.evidence_quote," +
        " f.rationale, f.model, f.prompt_version," +
        " (f.description_sha256 <> encode(sha256(i.description::bytea),'hex')) AS stale," +
        " i.description, i.type_code, i.severity_norm," +
        " i.incident_date::text AS incident_date, i.location" +
        " FROM incident_ai_findings f JOIN incidents i ON f.source_row = i.source_row" +
        " ORDER BY f.is_psychosocial DESC, f.severity_mismatch DESC, f.id",
    );
    res.json(rows);
  } catch (err) {
    next(err);
  }
});

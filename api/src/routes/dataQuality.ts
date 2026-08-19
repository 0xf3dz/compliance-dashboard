import { Router } from "express";
import { query } from "../db";

export const dataQualityRouter = Router();

interface IssueRow {
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

interface IssueTypeGroup {
  issue_type: string;
  action: string;
  count: number;
  items: IssueRow[];
}

interface FileGroup {
  source_file: string;
  count: number;
  issue_types: IssueTypeGroup[];
}

dataQualityRouter.get("/", async (_req, res, next) => {
  try {
    const rows = await query<IssueRow>(
      "SELECT id, source_file, source_row, record_ref, issue_type, field," +
        " raw_value, action, resolution, detail FROM data_quality_issues" +
        " ORDER BY source_file, issue_type, id",
    );

    const files: Record<string, FileGroup> = {};
    for (const r of rows) {
      const file = (files[r.source_file] ??= {
        source_file: r.source_file,
        count: 0,
        issue_types: [],
      });
      file.count += 1;
      let group = file.issue_types.find((g) => g.issue_type === r.issue_type);
      if (!group) {
        group = {
          issue_type: r.issue_type,
          action: r.action,
          count: 0,
          items: [],
        };
        file.issue_types.push(group);
      }
      group.count += 1;
      group.items.push(r);
    }

    res.json({ total: rows.length, files: Object.values(files) });
  } catch (err) {
    next(err);
  }
});

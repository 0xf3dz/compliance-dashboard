// Integration tests. These need a live database with the pipeline already run:
//
//   docker compose up -d && python -m ingestion.ingest
//
// They drive the imported app through supertest, so no port is bound and no
// server process is left behind. CI runs the same three commands in order.

import { afterAll, describe, expect, it } from "vitest";
import request from "supertest";
import { app } from "../src/app";
import { pool } from "../src/db";

afterAll(async () => {
  await pool.end();
});

interface MonthRow {
  month: string;
  scope1_kg: number;
  scope2_kg: number;
  scope2_flagged_kwh: number;
  scope1_has_deliveries: boolean;
}

interface Caveat {
  code: string;
  message: string;
  months: string[];
}

describe("GET /health", () => {
  it("returns ok", async () => {
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true });
  });
});

describe("GET /api/emissions/monthly", () => {
  it("returns the 18 months of the window", async () => {
    const res = await request(app).get("/api/emissions/monthly");
    expect(res.status).toBe(200);
    const rows: MonthRow[] = res.body;
    expect(rows).toHaveLength(18);
    expect(rows[0].month).toBe("2025-01");
    expect(rows[17].month).toBe("2026-06");
  });

  it("marks 2025-11 as a month without fuel deliveries", async () => {
    // Problem D3. The month has no fuel invoice, so its Scope 1 is a floor.
    // Without this flag the chart shows a real zero, which is a false claim.
    const res = await request(app).get("/api/emissions/monthly");
    const rows: MonthRow[] = res.body;
    const nov = rows.find((r) => r.month === "2025-11");
    expect(nov).toBeDefined();
    expect(nov?.scope1_has_deliveries).toBe(false);
    expect(rows.filter((r) => !r.scope1_has_deliveries).map((r) => r.month)).toEqual([
      "2025-11",
    ]);
  });

  it("reports flagged electricity in at least nine months", async () => {
    // Defect 1. The MTR-07 collapse reaches the API instead of stopping at the
    // database, so the panel can mark the months it affects.
    const res = await request(app).get("/api/emissions/monthly");
    const rows: MonthRow[] = res.body;
    const flagged = rows.filter((r) => r.scope2_flagged_kwh > 0);
    expect(flagged.length).toBeGreaterThanOrEqual(9);
    expect(flagged.every((r) => r.scope2_kg > 0)).toBe(true);
  });
});

describe("GET /api/emissions/summary", () => {
  it("returns totals and shares that agree with each other", async () => {
    const res = await request(app).get("/api/emissions/summary");
    expect(res.status).toBe(200);
    expect(res.body.months).toBe(18);
    expect(res.body.total_kg).toBe(res.body.scope1_kg + res.body.scope2_kg);
    expect(res.body.scope1_share + res.body.scope2_share).toBeCloseTo(1, 2);
  });

  it("carries the data quality caveats", async () => {
    const res = await request(app).get("/api/emissions/summary");
    const caveats: Caveat[] = res.body.caveats;
    const codes = caveats.map((c) => c.code);
    expect(codes).toContain("suspected_scale_error");
    expect(codes).toContain("missing_fuel_month");
  });

  it("gives every caveat a message and the months it applies to", async () => {
    const res = await request(app).get("/api/emissions/summary");
    const caveats: Caveat[] = res.body.caveats;
    expect(caveats.length).toBeGreaterThan(0);
    for (const c of caveats) {
      expect(c.message.length).toBeGreaterThan(20);
      expect(c.months.length).toBeGreaterThan(0);
      for (const m of c.months) expect(m).toMatch(/^\d{4}-\d{2}$/);
    }
  });

  it("names the month inside the missing fuel caveat", async () => {
    // The frontend prints this text. The server owns it, so no panel has to
    // hardcode a month that a later export can change.
    const res = await request(app).get("/api/emissions/summary");
    const caveats: Caveat[] = res.body.caveats;
    const fuel = caveats.find((c) => c.code === "missing_fuel_month");
    expect(fuel?.months).toEqual(["2025-11"]);
    expect(fuel?.message).toContain("2025-11");
    expect(fuel?.message).toContain("lower bound");
  });

  it("agrees with the monthly rows about which months are affected", async () => {
    const [summary, monthly] = await Promise.all([
      request(app).get("/api/emissions/summary"),
      request(app).get("/api/emissions/monthly"),
    ]);
    const caveats: Caveat[] = summary.body.caveats;
    const rows: MonthRow[] = monthly.body;
    const scale = caveats.find((c) => c.code === "suspected_scale_error");
    const flaggedMonths = rows.filter((r) => r.scope2_flagged_kwh > 0).map((r) => r.month);
    expect(scale?.months).toEqual(flaggedMonths);
  });
});

describe("GET /api/incidents", () => {
  it("summarises all 42 incidents", async () => {
    const res = await request(app).get("/api/incidents/summary");
    expect(res.status).toBe(200);
    expect(res.body.total).toBe(42);
    expect(res.body.trend).toHaveLength(18);
  });

  it("orders severity from Low to High", async () => {
    // The two planted rank mismatches invert the scale if anybody calibrates
    // on them. The order comes from severity_rank, so it cannot flip.
    const res = await request(app).get("/api/incidents/summary");
    const keys = res.body.by_severity.map((r: { key: string }) => r.key);
    expect(keys).toEqual(["Low", "Medium", "High"]);
  });

  it("returns AI findings whose quotes are verbatim and not stale", async () => {
    const res = await request(app).get("/api/incidents/ai-findings");
    expect(res.status).toBe(200);
    const rows: { evidence_quote: string; description: string; stale: boolean }[] = res.body;
    if (rows.length === 0) return; // The AI layer needs a key; layers 1, 2 and 4 do not.
    for (const r of rows) {
      expect(r.description).toContain(r.evidence_quote);
      expect(r.stale).toBe(false);
    }
  });
});

describe("GET /api/data-quality", () => {
  it("returns every issue with its items", async () => {
    const res = await request(app).get("/api/data-quality/");
    expect(res.status).toBe(200);
    expect(res.body.total).toBeGreaterThanOrEqual(63);
    expect(res.body.files.length).toBeGreaterThan(0);
    for (const f of res.body.files) {
      for (const g of f.issue_types) {
        expect(g.items.length).toBe(g.count);
        expect(g.items.length).toBeGreaterThan(0);
      }
    }
  });

  it("reports when the data was ingested", async () => {
    const res = await request(app).get("/api/data-quality/");
    // The footer prints this stamp. A wrong date format renders as "Invalid
    // Date" on the page, so the test parses it instead of trusting the type.
    expect(Number.isNaN(Date.parse(res.body.ingested_at))).toBe(false);
  });

  it("counts the same total three ways", async () => {
    const res = await request(app).get("/api/data-quality/");
    const files: { count: number; issue_types: { count: number }[] }[] = res.body.files;
    const byFile = files.reduce((s, f) => s + f.count, 0);
    const byGroup = files.reduce(
      (s, f) => s + f.issue_types.reduce((t, g) => t + g.count, 0),
      0,
    );
    expect(byFile).toBe(res.body.total);
    expect(byGroup).toBe(res.body.total);
  });

  it("includes the five detectors the draft missed", async () => {
    const res = await request(app).get("/api/data-quality/");
    const files: { issue_types: { issue_type: string }[] }[] = res.body.files;
    const types = files.flatMap((f) => f.issue_types.map((g) => g.issue_type));
    for (const t of [
      "missing_fuel_month",
      "recycled_description",
      "inconsistent_severity_for_identical_text",
      "supplier_category_conflict",
      "expected_anomaly",
    ]) {
      expect(types).toContain(t);
    }
  });
});

describe("POST /api/feature-requests", () => {
  it("stores a complete request", async () => {
    const res = await request(app).post("/api/feature-requests/").send({
      name: "Test Reader",
      email: "reader@example.com",
      message: "A test request from the suite.",
    });
    expect(res.status).toBe(201);
    expect(res.body.id).toBeGreaterThan(0);
    // The row is proof, not product data, so the test removes it again.
    await pool.query("DELETE FROM feature_requests WHERE id = $1", [res.body.id]);
  });

  it("rejects a request without a message", async () => {
    const res = await request(app)
      .post("/api/feature-requests/")
      .send({ name: "Test Reader", email: "reader@example.com" });
    expect(res.status).toBe(400);
  });

  it("rejects an invalid email", async () => {
    const res = await request(app)
      .post("/api/feature-requests/")
      .send({ name: "Test Reader", email: "not-an-email", message: "Hi" });
    expect(res.status).toBe(400);
  });
});

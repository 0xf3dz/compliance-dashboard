import { Router } from "express";
import { query } from "../db";

export const featureRequestsRouter = Router();

// Reader-supplied data, not derivable from the CSVs, so this table follows the
// schema_ai.sql rule: created here, never dropped by a re-ingest. The API owns
// the table because the API is its only writer, and creating it on first use
// keeps the run instructions unchanged.
const DDL =
  "CREATE TABLE IF NOT EXISTS feature_requests (" +
  " id serial PRIMARY KEY," +
  " name text NOT NULL," +
  " email text NOT NULL," +
  " message text NOT NULL," +
  " created_at timestamptz NOT NULL DEFAULT now()" +
  ")";

let ensured: Promise<unknown> | null = null;
function ensureTable(): Promise<unknown> {
  ensured ??= query(DDL);
  return ensured;
}

// Server-side, because the browser check is decoration. Length caps stop a
// pasted novel from reaching the table.
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const LIMITS = { name: 200, email: 320, message: 5000 };

function clean(value: unknown, max: number): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 && trimmed.length <= max ? trimmed : null;
}

featureRequestsRouter.post("/", async (req, res, next) => {
  try {
    const name = clean(req.body?.name, LIMITS.name);
    const email = clean(req.body?.email, LIMITS.email);
    const message = clean(req.body?.message, LIMITS.message);
    if (!name || !message || !email || !EMAIL.test(email)) {
      res.status(400).json({
        error: "name, a valid email and a message are required",
      });
      return;
    }

    await ensureTable();
    const rows = await query<{ id: number }>(
      "INSERT INTO feature_requests (name, email, message) VALUES ($1, $2, $3) RETURNING id",
      [name, email, message],
    );
    res.status(201).json({ id: rows[0].id });
  } catch (err) {
    next(err);
  }
});

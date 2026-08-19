import { fileURLToPath } from "node:url";

import dotenv from "dotenv";
import pg from "pg";

// Load the repository-root .env before reading DATABASE_URL. ingestion/db.py
// loads the same file, so the Node and Python layers cannot end up pointed at
// two different databases. Every database consumer imports this module, which
// makes it the one place the load has to happen.
dotenv.config({ path: fileURLToPath(new URL("../../.env", import.meta.url)) });

const { Pool } = pg;

const DATABASE_URL =
  process.env.DATABASE_URL ??
  "postgresql://ironbark:ironbark@localhost:5544/ironbark";

export const pool = new Pool({ connectionString: DATABASE_URL });

export async function query<T extends pg.QueryResultRow = pg.QueryResultRow>(
  text: string,
  params?: unknown[],
): Promise<T[]> {
  const res = await pool.query<T>(text, params);
  return res.rows;
}

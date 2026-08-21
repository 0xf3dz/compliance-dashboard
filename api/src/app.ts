// The express application, without listen. index.ts binds the port and
// tests/routes.test.ts imports this object, so the integration test drives the
// real routes without a live port and without a spare process to clean up.

import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import cors from "cors";
import express, {
  type NextFunction,
  type Request,
  type Response,
} from "express";
import { dataQualityRouter } from "./routes/dataQuality";
import { emissionsRouter } from "./routes/emissions";
import { featureRequestsRouter } from "./routes/featureRequests";
import { incidentsRouter } from "./routes/incidents";

export const app = express();

app.use(cors({ origin: ["http://localhost:5173", "http://127.0.0.1:5173"] }));
app.use(express.json());

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.use("/api/emissions", emissionsRouter);
app.use("/api/incidents", incidentsRouter);
app.use("/api/data-quality", dataQualityRouter);
app.use("/api/feature-requests", featureRequestsRouter);

// In the deployed build the API also serves the compiled frontend, so the whole
// app answers on one origin and runs as one Railway service. frontend/dist only
// exists after a frontend build, so this block is skipped in dev and tests,
// where the frontend runs on its own port.
const frontendDist = fileURLToPath(
  new URL("../../frontend/dist", import.meta.url),
);
if (existsSync(frontendDist)) {
  app.use(express.static(frontendDist));
  // Deep links fall back to index.html. /api and /health are matched above, so
  // the single-page app never shadows them.
  app.get(/^(?!\/(?:api|health)).*/, (_req, res) => {
    res.sendFile(path.join(frontendDist, "index.html"));
  });
}

// A route that throws sends the message to the client. The factor loader and
// the fuel factor both throw on purpose, and a silent 500 would hide the one
// signal that says the data no longer supports the numbers.
app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  const message = err instanceof Error ? err.message : "internal error";
  console.error("API error:", message);
  res.status(500).json({ error: message });
});

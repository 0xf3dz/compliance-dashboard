// Server entry point. The application lives in app.ts, which the tests import.
// This file only binds the port.

import { app } from "./app";

const PORT = Number(process.env.PORT ?? 3000);

app.listen(PORT, () => {
  console.log(`Ironbark API listening on http://localhost:${PORT}`);
});

import createMiddleware from "next-intl/middleware";

import { routing } from "./i18n/routing";

/** Redirects unprefixed page paths to English. */
export default createMiddleware(routing);

export const config = {
  // Everything except API routes, Next.js internals, the DuckDB-WASM bundle and files with an extension.
  matcher: ["/((?!api|_next|duckdb|.*\\..*).*)"],
};

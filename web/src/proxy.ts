import createMiddleware from "next-intl/middleware";

import { routing } from "./i18n/routing";

/** Redirects unprefixed paths to the visitor's language (Accept-Language, then English) and sets hreflang links. */
export default createMiddleware(routing);

export const config = {
  // Everything except API routes, Next.js internals, the DuckDB-WASM bundle and files with an extension.
  matcher: ["/((?!api|_next|_vercel|duckdb|.*\\..*).*)"],
};

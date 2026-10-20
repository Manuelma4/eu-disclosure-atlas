import type { Metadata } from "next";
import { getLocale } from "next-intl/server";

import { routing } from "@/i18n/routing";

/** Public origin of the website, for canonical and hreflang URLs. */
export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

/** Canonical URL of `path` in the current language and its alternates in the others (hreflang). */
export async function alternates(path: string): Promise<Metadata["alternates"]> {
  const locale = await getLocale();
  return {
    canonical: `/${locale}${path}`,
    languages: {
      ...Object.fromEntries(routing.locales.map((l) => [l, `/${l}${path}`])),
      "x-default": `/${routing.defaultLocale}${path}`,
    },
  };
}

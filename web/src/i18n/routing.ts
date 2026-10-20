import { defineRouting } from "next-intl/routing";

export const routing = defineRouting({
  locales: ["en", "fr", "es", "de"],
  defaultLocale: "en",
  localePrefix: "always",
});

export type Locale = (typeof routing.locales)[number];

/** Intl locale used for numbers and dates in each UI language. */
export const INTL_LOCALE: Record<Locale, string> = { en: "en-GB", fr: "fr-FR", es: "es-ES", de: "de-DE" };

export const LOCALE_NAMES: Record<Locale, string> = { en: "English", fr: "Français", es: "Español", de: "Deutsch" };

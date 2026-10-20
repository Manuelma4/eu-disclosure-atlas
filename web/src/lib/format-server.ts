import { getLocale, getTranslations } from "next-intl/server";

import { createFormat } from "./format";

/** Formatters for the request's locale, in Server Components. */
export async function getFormat() {
  return createFormat(await getLocale());
}

/** Translated labels for enum values coming from the API (roles, natures, instruments). */
export async function getLabels() {
  const [roles, natures, instruments] = await Promise.all([
    getTranslations("roles"),
    getTranslations("natures"),
    getTranslations("instruments"),
  ]);
  return {
    role: (value: string) => (roles.has(value as never) ? roles(value as never) : value),
    nature: (value: string) => (natures.has(value as never) ? natures(value as never) : value),
    instrument: (value: string) => (instruments.has(value as never) ? instruments(value as never) : value),
  };
}

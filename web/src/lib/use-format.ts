"use client";

import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";

import { createFormat } from "./format";

/** Formatters for the current locale, in Client Components. */
export function useFormat() {
  const locale = useLocale();
  return useMemo(() => createFormat(locale), [locale]);
}

/** Translated labels for enum values coming from the API (roles, natures, instruments). */
export function useLabels() {
  const roles = useTranslations("roles");
  const natures = useTranslations("natures");
  const instruments = useTranslations("instruments");
  return useMemo(
    () => ({
      role: (value: string) => (roles.has(value as never) ? roles(value as never) : value),
      nature: (value: string) => (natures.has(value as never) ? natures(value as never) : value),
      instrument: (value: string) => (instruments.has(value as never) ? instruments(value as never) : value),
    }),
    [roles, natures, instruments],
  );
}

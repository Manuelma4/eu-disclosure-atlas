import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { SignInCallback } from "@/components/account/sign-in-callback";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("account");
  return { title: t("signingInTitle"), robots: { index: false } };
}

export default function CallbackPage() {
  return <SignInCallback />;
}

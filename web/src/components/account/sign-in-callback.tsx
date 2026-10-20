"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { completeSignIn } from "@/lib/auth";

export function SignInCallback() {
  const t = useTranslations("account");
  // Plain Next.js router: `returnTo` is the full path, locale prefix included.
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // the authorization code is single-use (Strict Mode runs effects twice)
    started.current = true;
    completeSignIn(window.location.search)
      .then((returnTo) => router.replace(returnTo))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : ""));
  }, [router]);

  return (
    <p className="text-ink-2" role="status">
      {error !== null ? t("signInFailed", { message: error }) : t("signingIn")}
    </p>
  );
}

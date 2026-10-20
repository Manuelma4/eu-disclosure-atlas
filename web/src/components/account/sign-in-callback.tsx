"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { completeSignIn } from "@/lib/auth";

export function SignInCallback() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // the authorization code is single-use (Strict Mode runs effects twice)
    started.current = true;
    completeSignIn(window.location.search)
      .then((returnTo) => router.replace(returnTo))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Sign-in failed"));
  }, [router]);

  return (
    <p className="text-ink-2" role="status">
      {error ? `Sign-in failed: ${error}` : "Signing you in…"}
    </p>
  );
}

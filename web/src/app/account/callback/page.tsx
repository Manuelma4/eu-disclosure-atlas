import type { Metadata } from "next";

import { SignInCallback } from "@/components/account/sign-in-callback";

export const metadata: Metadata = { title: "Signing in", robots: { index: false } };

export default function CallbackPage() {
  return <SignInCallback />;
}

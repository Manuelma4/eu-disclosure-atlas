"use client";

import Link from "next/link";

import { signIn, signOut, useSession } from "@/lib/auth";

const item = "rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink";

/** Header entry: "Sign in", or the watchlist link and "Sign out". */
export function AccountMenu() {
  const session = useSession();
  if (session === undefined) return null; // server render: decided in the browser
  if (!session) {
    return (
      <button type="button" className={item} onClick={() => signIn()}>
        Sign in
      </button>
    );
  }
  return (
    <>
      <Link className={item} href="/account/watchlist">
        Watchlist
      </Link>
      <button type="button" className={`hidden sm:block ${item}`} onClick={() => signOut()}>
        Sign out
      </button>
    </>
  );
}

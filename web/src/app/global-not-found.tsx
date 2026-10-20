import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = { title: "Not found · EU Disclosure Atlas" };

/** Requests that match no route at all (the proxy sends everything else to a locale). */
export default function GlobalNotFound() {
  return (
    <html lang="en">
      <body className="font-sans">
        <main className="mx-auto max-w-lg px-4 py-24 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">Not in the Atlas</h1>
          <p className="mt-3 text-ink-2">No page matches this address.</p>
          <Link href="/en" className="mt-6 inline-block rounded-md bg-ink px-4 py-2 text-sm font-medium text-page">
            EU Disclosure Atlas
          </Link>
        </main>
      </body>
    </html>
  );
}

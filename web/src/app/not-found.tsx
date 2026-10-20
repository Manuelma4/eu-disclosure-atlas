import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-lg py-24 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">Not in the Atlas</h1>
      <p className="mt-3 text-ink-2">
        No issuer or page matches this address. Coverage grows regulator by regulator.
      </p>
      <Link href="/filings" className="mt-6 inline-block rounded-md bg-ink px-4 py-2 text-sm font-medium text-page hover:opacity-90">
        Search filings
      </Link>
    </div>
  );
}

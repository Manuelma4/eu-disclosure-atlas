"use client";

import { useEffect } from "react";

export default function Error({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto max-w-lg py-24 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">The data is not reachable right now</h1>
      <p className="mt-3 text-ink-2">
        The website reads everything from the public API. It may be restarting; your page will work again in a moment.
      </p>
      <button
        type="button"
        onClick={() => retry()}
        className="mt-6 rounded-md bg-ink px-4 py-2 text-sm font-medium text-page hover:opacity-90"
      >
        Try again
      </button>
    </div>
  );
}

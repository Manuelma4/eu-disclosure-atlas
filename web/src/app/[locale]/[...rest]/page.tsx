import { notFound } from "next/navigation";

// This segment only ever renders the not-found page, so there is no instant navigation to validate.
export const instant = false;

/** Unknown paths inside a locale render that locale's not-found page. */
export default function CatchAll() {
  notFound();
}

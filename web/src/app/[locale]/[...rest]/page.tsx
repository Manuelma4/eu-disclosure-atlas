import { notFound } from "next/navigation";

/** Unknown paths inside a locale render that locale's not-found page. */
export default function CatchAll() {
  notFound();
}

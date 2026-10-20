"use client";

import { useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import { signIn, signOut, useSession } from "@/lib/auth";

const styles = {
  bar: "shrink-0 whitespace-nowrap rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink",
  menu: "flex min-h-11 w-full items-center rounded-md px-3 text-left hover:bg-surface-2 hover:text-ink",
};

/** Header entry: "Sign in", or the watchlist link and "Sign out". */
export function AccountMenu({ variant = "bar" }: { variant?: "bar" | "menu" }) {
  const item = styles[variant];
  const t = useTranslations("account");
  const session = useSession();
  if (session === undefined) return null; // server render: decided in the browser
  if (!session) {
    return (
      <button type="button" className={item} onClick={() => signIn()}>
        {t("signIn")}
      </button>
    );
  }
  return (
    <>
      <Link className={item} href="/account/watchlist">
        {t("watchlist")}
      </Link>
      <button type="button" className={item} onClick={() => signOut()}>
        {t("signOut")}
      </button>
    </>
  );
}

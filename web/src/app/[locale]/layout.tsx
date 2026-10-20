import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";

import { SiteFooter, SiteHeader } from "@/components/site-header";
import { routing } from "@/i18n/routing";
import { alternates, SITE_URL } from "@/lib/seo";

import "../globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");
  return {
    metadataBase: new URL(SITE_URL),
    title: { default: t("title"), template: `%s · ${t("siteName")}` },
    description: t("description"),
    applicationName: t("siteName"),
    alternates: await alternates(""),
  };
}

export default async function RootLayout({ children }: LayoutProps<"/[locale]">) {
  const locale = await getLocale();
  const t = await getTranslations("nav");
  return (
    <html lang={locale} className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col font-sans">
        <NextIntlClientProvider>
          <a
            href="#content"
            className="sr-only z-50 rounded-md bg-ink px-3 py-2 text-sm font-medium text-page focus:not-sr-only focus:fixed focus:left-4 focus:top-3"
          >
            {t("skip")}
          </a>
          <SiteHeader />
          <main id="content" tabIndex={-1} className="mx-auto min-h-[calc(100svh-3.5rem)] w-full max-w-6xl flex-1 px-4 pt-8 outline-none sm:px-6">
            {children}
          </main>
          <SiteFooter />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}

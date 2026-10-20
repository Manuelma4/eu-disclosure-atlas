import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { SiteFooter, SiteHeader } from "@/components/site-header";

import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "EU Disclosure Atlas: insider trades across Europe", template: "%s · EU Disclosure Atlas" },
  description:
    "Open, unified data on insider trading and short selling across European markets, published by national " +
    "regulators under MAR and the Short Selling Regulation. Live, pseudonymized, research-grade.",
  applicationName: "EU Disclosure Atlas",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col font-sans">
        <SiteHeader />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 pt-8 sm:px-6">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}

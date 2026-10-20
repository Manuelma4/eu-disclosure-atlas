"use client";

import { Suspense, type ComponentProps } from "react";

import { Link, usePathname } from "@/i18n/navigation";

type Props = ComponentProps<typeof Link> & { href: string; variant?: "bar" | "menu" };
const currentStyle =
  "aria-[current=page]:text-ink aria-[current=page]:underline aria-[current=page]:decoration-accent aria-[current=page]:decoration-2 aria-[current=page]:underline-offset-[6px]";
const styles = {
  bar: `shrink-0 whitespace-nowrap rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink ${currentStyle}`,
  menu: `flex min-h-11 items-center rounded-md px-3 hover:bg-surface-2 hover:text-ink ${currentStyle}`,
};

/** Main-navigation link that marks the current section with aria-current (streams in on dynamic routes). */
export function NavLink({ className = "", variant = "bar", ...props }: Props) {
  const base = styles[variant];
  return (
    <Suspense fallback={<Link className={`${base} ${className}`} {...props} />}>
      <ActiveLink className={`${base} ${className}`} {...props} />
    </Suspense>
  );
}

function ActiveLink({ href, className = "", ...props }: Omit<Props, "variant">) {
  const pathname = usePathname();
  const current = href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
  return <Link href={href} aria-current={current ? "page" : undefined} className={className} {...props} />;
}

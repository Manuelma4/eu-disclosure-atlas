"use client";

import { Suspense, type ComponentProps } from "react";

import { Link, usePathname } from "@/i18n/navigation";

type Props = ComponentProps<typeof Link> & { href: string };
const base = "shrink-0 rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink aria-[current=page]:text-ink aria-[current=page]:underline aria-[current=page]:decoration-accent aria-[current=page]:decoration-2 aria-[current=page]:underline-offset-[6px]";

/** Main-navigation link that marks the current section with aria-current (streams in on dynamic routes). */
export function NavLink({ className = "", ...props }: Props) {
  return (
    <Suspense fallback={<Link className={`${base} ${className}`} {...props} />}>
      <ActiveLink className={className} {...props} />
    </Suspense>
  );
}

function ActiveLink({ href, className = "", ...props }: Props) {
  const pathname = usePathname();
  const current = href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
  return <Link href={href} aria-current={current ? "page" : undefined} className={`${base} ${className}`} {...props} />;
}

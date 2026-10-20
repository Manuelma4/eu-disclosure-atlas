/** Client for the alerts service (Go): watchlists, alert rules, webhook endpoints, deliveries. */
import { accessToken } from "./auth";

const ALERTS_URL = process.env.NEXT_PUBLIC_ATLAS_ALERTS_URL ?? "http://localhost:8091";

export type Watchlist = { id: string; name: string; issuers: string[]; created_at: string };
export type Rule = {
  id: string;
  name: string;
  watchlist_id: string | null;
  natures: string[];
  roles: string[];
  countries: string[];
  open_market_only: boolean;
  min_amount_eur: number | null;
  email: string | null;
  webhook_endpoint_id: string | null;
  enabled: boolean;
  created_at: string;
};
export type Endpoint = { id: string; url: string; description: string; enabled: boolean; secret?: string };
export type Delivery = {
  id: string;
  rule_id: string;
  filing_id: string;
  channel: "webhook" | "email";
  target: string;
  status: "pending" | "retrying" | "delivered" | "failed";
  attempts: number;
  response_status: number | null;
  last_error: string | null;
  created_at: string;
};

export class AlertsError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function alerts<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const token = await accessToken();
  if (!token) throw new AlertsError("Please sign in", 401);
  const response = await fetch(`${ALERTS_URL}${path}`, {
    method: init.method ?? "GET",
    headers: { Authorization: `Bearer ${token}`, ...(init.body ? { "Content-Type": "application/json" } : {}) },
    body: init.body ? JSON.stringify(init.body) : undefined,
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new AlertsError(problem?.detail ?? response.statusText, response.status);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

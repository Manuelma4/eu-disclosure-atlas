"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { Card } from "@/components/ui";
import { alerts, type Delivery, type Endpoint, type Rule, type Watchlist } from "@/lib/alerts";
import { signIn, useSession } from "@/lib/auth";

type Data = { watchlist: Watchlist | null; rules: Rule[]; endpoints: Endpoint[]; deliveries: Delivery[] };

const input = "rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink";
const button = "rounded-md bg-ink px-3 py-1.5 text-sm font-medium text-surface hover:opacity-90 disabled:opacity-50";

async function load(): Promise<Data> {
  const [lists, rules, endpoints, deliveries] = await Promise.all([
    alerts<{ items: Watchlist[] }>("/v1/watchlists"),
    alerts<{ items: Rule[] }>("/v1/rules"),
    alerts<{ items: Endpoint[] }>("/v1/webhook-endpoints"),
    alerts<{ items: Delivery[] }>("/v1/deliveries?limit=20"),
  ]);
  return { watchlist: lists.items[0] ?? null, rules: rules.items, endpoints: endpoints.items, deliveries: deliveries.items };
}

export function WatchlistManager() {
  const session = useSession();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);

  const refresh = useCallback(() => {
    load()
      .then((next) => {
        setData(next);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not reach the alerts service"));
  }, []);

  useEffect(() => {
    if (session) refresh();
  }, [session, refresh]);

  const run = async (action: () => Promise<unknown>) => {
    try {
      await action();
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    }
  };

  if (session === undefined) return null;
  if (!session) {
    return (
      <Card title="Sign in to manage alerts" subtitle="Accounts use passkeys or a password, managed by Keycloak.">
        <button type="button" className={button} onClick={() => signIn()}>
          Sign in
        </button>
      </Card>
    );
  }

  const addIssuer = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const lei = String(new FormData(form).get("lei") ?? "").trim().toUpperCase();
    void run(async () => {
      if (data?.watchlist) {
        await alerts(`/v1/watchlists/${data.watchlist.id}/issuers/${encodeURIComponent(lei)}`, { method: "PUT" });
      } else {
        await alerts("/v1/watchlists", { method: "POST", body: { name: "My watchlist", issuers: [lei] } });
      }
      form.reset();
    });
  };

  const addRule = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const nature = String(values.get("nature"));
    const minimum = String(values.get("min_amount_eur") ?? "");
    const email = String(values.get("email") ?? "").trim();
    const webhookUrl = String(values.get("webhook_url") ?? "").trim();
    void run(async () => {
      let endpointId: string | null = null;
      if (webhookUrl) {
        const endpoint = await alerts<Endpoint>("/v1/webhook-endpoints", { method: "POST", body: { url: webhookUrl } });
        endpointId = endpoint.id;
        setSecret(endpoint.secret ?? null);
      }
      await alerts("/v1/rules", {
        method: "POST",
        body: {
          name: String(values.get("name")),
          watchlist_id: data?.watchlist?.id ?? null,
          natures: nature === "any" ? [] : [nature],
          min_amount_eur: minimum ? Number(minimum) : null,
          email: email || null,
          webhook_endpoint_id: endpointId,
        },
      });
      form.reset();
    });
  };

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {error && (
        <p role="alert" className="rounded-md border border-hairline bg-surface-2 px-4 py-2 text-sm text-ink lg:col-span-2">
          {error}
        </p>
      )}

      <Card title="Issuers" subtitle="Legal Entity Identifiers (20 characters), as shown on company pages.">
        <ul className="divide-y divide-hairline text-sm">
          {(data?.watchlist?.issuers ?? []).map((lei) => (
            <li key={lei} className="flex items-center justify-between py-2">
              <Link href={`/company/${lei}`} className="font-mono text-ink hover:underline">
                {lei}
              </Link>
              <button
                type="button"
                className="text-ink-3 hover:text-ink"
                onClick={() => run(() => alerts(`/v1/watchlists/${data!.watchlist!.id}/issuers/${lei}`, { method: "DELETE" }))}
              >
                Remove
              </button>
            </li>
          ))}
          {data && !data.watchlist?.issuers.length && <li className="py-2 text-ink-3">No issuers yet.</li>}
        </ul>
        <form onSubmit={addIssuer} className="mt-3 flex gap-2">
          <label className="sr-only" htmlFor="lei">
            Issuer LEI
          </label>
          <input id="lei" name="lei" required pattern="[0-9A-Za-z]{18}[0-9]{2}" placeholder="549300…" className={`${input} flex-1 font-mono`} />
          <button className={button}>Add</button>
        </form>
      </Card>

      <Card title="Alert rules" subtitle="A rule watches the issuers on your list.">
        <ul className="divide-y divide-hairline text-sm">
          {(data?.rules ?? []).map((rule) => (
            <li key={rule.id} className="flex items-start justify-between gap-3 py-2">
              <div>
                <p className="font-medium text-ink">{rule.name}</p>
                <p className="text-xs text-ink-3">
                  {rule.natures.length ? rule.natures.join(", ") : "any nature"}
                  {rule.min_amount_eur ? ` · from EUR ${rule.min_amount_eur.toLocaleString("en")}` : ""}
                  {rule.email ? ` · email ${rule.email}` : ""}
                  {rule.webhook_endpoint_id ? " · webhook" : ""}
                </p>
              </div>
              <button type="button" className="text-ink-3 hover:text-ink" onClick={() => run(() => alerts(`/v1/rules/${rule.id}`, { method: "DELETE" }))}>
                Delete
              </button>
            </li>
          ))}
          {data && !data.rules.length && <li className="py-2 text-ink-3">No rules yet.</li>}
        </ul>
        <form onSubmit={addRule} className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
          <input name="name" required maxLength={100} placeholder="Rule name, e.g. CEO buys" aria-label="Rule name" className={`${input} sm:col-span-2`} />
          <select name="nature" aria-label="Nature" className={input} defaultValue="buy">
            <option value="buy">Buys</option>
            <option value="sell">Sells</option>
            <option value="any">Any nature</option>
          </select>
          <input name="min_amount_eur" type="number" min={0} step={1000} placeholder="Minimum EUR" aria-label="Minimum amount in euros" className={input} />
          <input name="email" type="email" defaultValue={session.email} placeholder="Email" aria-label="Email" className={input} />
          <input name="webhook_url" type="url" placeholder="Webhook URL (optional)" aria-label="Webhook URL" className={input} />
          <button className={`${button} sm:col-span-2`} disabled={!data?.watchlist}>
            Create rule
          </button>
        </form>
        {secret && (
          <p className="mt-3 rounded-md bg-surface-2 px-3 py-2 text-xs text-ink-2">
            Webhook signing secret (shown once): <code className="break-all font-mono text-ink">{secret}</code>
          </p>
        )}
      </Card>

      <Card title="Recent deliveries" className="lg:col-span-2">
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-ink-3">
            <tr>
              <th className="py-1 font-normal">When</th>
              <th className="py-1 font-normal">Channel</th>
              <th className="py-1 font-normal">Status</th>
              <th className="py-1 font-normal">Attempts</th>
              <th className="py-1 font-normal">Filing</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {(data?.deliveries ?? []).map((d) => (
              <tr key={d.id}>
                <td className="py-1.5 text-ink-2">{new Date(d.created_at).toLocaleString("en-GB")}</td>
                <td className="py-1.5">{d.channel}</td>
                <td className="py-1.5">{d.status}</td>
                <td className="py-1.5 tabular">{d.attempts}</td>
                <td className="py-1.5 font-mono text-xs text-ink-3">{d.filing_id.slice(0, 12)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && !data.deliveries.length && <p className="pt-2 text-sm text-ink-3">Nothing delivered yet.</p>}
      </Card>
    </div>
  );
}

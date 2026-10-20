/**
 * Server-side data access. The website is a client of the public REST API (no private shortcuts),
 * through a client generated from the OpenAPI document. Every read is cached and tagged "snapshot";
 * the pipeline's snapshot.committed event calls POST /api/revalidate, which invalidates that tag.
 */
import { cacheLife, cacheTag } from "next/cache";
import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Transaction = components["schemas"]["Transaction"];
export type Overview = components["schemas"]["Overview"];
export type IssuerDetail = components["schemas"]["IssuerDetail"];
export type IssuerSummary = components["schemas"]["IssuerSummary"];
export type FlowPoint = components["schemas"]["FlowPoint"];
export type SourceStatus = components["schemas"]["SourceStatus"];
export type TransactionPage = components["schemas"]["Page_Transaction_"];
export type PriceSeries = components["schemas"]["PriceSeries"];
export type ExportFile = components["schemas"]["ExportFile"];
export type TransactionQuery = NonNullable<paths["/v1/insider-transactions"]["get"]["parameters"]["query"]>;

export const API_URL = process.env.ATLAS_API_URL ?? "http://127.0.0.1:8000";
const api = createClient<paths>({ baseUrl: API_URL });

class ApiError extends Error {
  constructor(path: string, status: number) {
    super(`API ${path} answered ${status}`);
  }
}

export async function getOverview(): Promise<Overview> {
  "use cache";
  cacheTag("snapshot");
  cacheLife("minutes");
  const { data, response } = await api.GET("/v1/stats/overview");
  if (!data) throw new ApiError("/v1/stats/overview", response.status);
  return data;
}

export async function getFlow(bucket: "day" | "week" | "month", days: number): Promise<FlowPoint[]> {
  "use cache";
  cacheTag("snapshot");
  cacheLife("minutes");
  const { data, response } = await api.GET("/v1/stats/flow", { params: { query: { bucket, days } } });
  if (!data) throw new ApiError("/v1/stats/flow", response.status);
  return data;
}

export async function getSources(): Promise<SourceStatus[]> {
  "use cache";
  cacheTag("snapshot");
  cacheLife("minutes");
  const { data, response } = await api.GET("/v1/sources");
  if (!data) throw new ApiError("/v1/sources", response.status);
  return data;
}

/** Returns null when the issuer does not exist (404). */
export async function getIssuer(lei: string): Promise<IssuerDetail | null> {
  "use cache";
  cacheTag("snapshot");
  cacheLife("minutes");
  const { data, response } = await api.GET("/v1/issuers/{lei}", { params: { path: { lei } } });
  if (response.status === 404) return null;
  if (!data) throw new ApiError("/v1/issuers/{lei}", response.status);
  return data;
}

export async function searchIssuers(q: string): Promise<IssuerSummary[]> {
  "use cache";
  cacheTag("snapshot");
  cacheLife("minutes");
  const { data, response } = await api.GET("/v1/issuers", { params: { query: { q, limit: 20 } } });
  if (!data) throw new ApiError("/v1/issuers", response.status);
  return data;
}

export async function getTransactions(query: TransactionQuery): Promise<TransactionPage> {
  "use cache";
  cacheTag("snapshot");
  cacheLife("minutes");
  const { data, response } = await api.GET("/v1/insider-transactions", { params: { query } });
  if (!data) throw new ApiError("/v1/insider-transactions", response.status);
  return data;
}

/** Daily prices and insider days for an issuer; null when the issuer does not exist (404). */
export async function getPrices(lei: string, days = 730): Promise<PriceSeries | null> {
  "use cache";
  cacheTag("snapshot");
  cacheLife("hours");
  const { data, response } = await api.GET("/v1/issuers/{lei}/prices", {
    params: { path: { lei }, query: { days } },
  });
  if (response.status === 404) return null;
  if (!data) throw new ApiError("/v1/issuers/{lei}/prices", response.status);
  return data;
}

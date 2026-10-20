import { revalidateTag } from "next/cache";

/**
 * Called when the pipeline commits a new data snapshot (NATS `atlas.snapshot.committed`, workstream P3).
 * Every cached read is tagged "snapshot", so one call refreshes the whole site with stale-while-revalidate.
 */
export async function POST(request: Request) {
  const secret = process.env.ATLAS_REVALIDATE_SECRET;
  if (!secret || request.headers.get("authorization") !== `Bearer ${secret}`) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }
  revalidateTag("snapshot", "max");
  return Response.json({ revalidated: true });
}

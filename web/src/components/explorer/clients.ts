/**
 * Mosaic clients that feed React state: the cross-filtered tables under each chart and the selection summary.
 * They query the same `filings` table as the charts and re-run whenever the shared selection changes.
 */
import { MosaicClient, type Selection } from "@uwdata/mosaic-core";
import { Query, sql, type FilterExpr } from "@uwdata/mosaic-sql";

export type Row = Record<string, string | number | Date | null>;

type Spec = { from: string; select: Record<string, string>; groupby?: string[]; orderby?: string[] };

const toRows = (data: unknown): Row[] => {
  const table = data as { toArray?: () => Row[] };
  return table?.toArray ? table.toArray().map((r) => ({ ...r })) : [];
};

/** Runs `spec` with the selection's filter and hands the rows to `onRows`. */
export class RowsClient extends MosaicClient {
  constructor(
    private readonly spec: Spec,
    selection: Selection,
    private readonly onRows: (rows: Row[]) => void,
    private readonly onError: (error: unknown) => void = console.error,
  ) {
    super(selection);
  }

  query(filter?: FilterExpr | null) {
    const select = Object.fromEntries(Object.entries(this.spec.select).map(([k, v]) => [k, sql`${v}`]));
    let query = Query.from(this.spec.from).select(select);
    if (filter) query = query.where(filter);
    if (this.spec.groupby) query = query.groupby(this.spec.groupby);
    if (this.spec.orderby) query = query.orderby(this.spec.orderby.map((o) => sql`${o}`));
    return query;
  }

  queryResult(data: unknown) {
    this.onRows(toRows(data));
    return this;
  }

  queryError(error: unknown) {
    this.onError(error);
    return this;
  }
}

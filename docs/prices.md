# Share prices

Company pages chart the daily closing price of the issuer's share with markers on the days its insiders bought
or sold on the market. The data comes from `GET /v1/issuers/{lei}/prices`
([src/disclosure_atlas/api/prices.py](../src/disclosure_atlas/api/prices.py)).

## How it works
1. **Which share.** The API selects the ISIN appearing most often in the issuer's share
   transactions and sorts ties by ISIN. Use `?isin=` to choose a different instrument.
   The request's `days` parameter accepts 30 to 1,830 calendar days and defaults to 730.
2. **ISIN to listing.** The provider resolves the ISIN to a ticker. The yfinance provider searches Yahoo Finance
   for the ISIN, keeps equities and prefers the issuer's home market by the ISIN country prefix (`SE` → `.ST`,
   `FR` → `.PA`, `DE` → `.DE`…; table `HOME_SUFFIX` in
   [reference/prices.py](../src/disclosure_atlas/reference/prices.py)).
3. **Cache.** The store requests 1,830 calendar days of history on refresh and saves it in
   `lake/reference/prices/<provider>/<ISIN>.parquet`. The Parquet metadata retains the listing,
   currency and fetch time, and each response is sliced to the requested date range.
   A cached entry is eligible for refresh after 12 hours. If refresh fails, the store
   serves the retained entry. A provider failure on a cold miss returns HTTP 502.
   Price-file writes leave the DuckLake snapshot ID unchanged.
4. **Insider days.** Open-market share purchases and sales of the issuer, aggregated by transaction date, in euros.

Prices are unadjusted closes of one listing in its trading currency. They are for context, not for returns
analysis (use the `research` extra and adjusted series for that). Nothing here is investment advice.

## Providers
Selected with `ATLAS_PRICE_PROVIDER`:

| Provider | Use | Notes |
|---|---|---|
| `yfinance` (default when installed: `pip install -e '.[prices]'`) | Development | Unofficial client of Yahoo Finance. Its maintainers state it is not affiliated with Yahoo and is meant for research and educational use, and refer users to Yahoo's terms of use ([yfinance README](https://github.com/ranaroussi/yfinance#legal-stuff)). Requests are sequential with at least one second between them. Do not use it for the public production site. |
| `synthetic` | Tests, demos, end-to-end runs | Deterministic random walk seeded by the ISIN, business days only. |
| `none` | Default without yfinance | Empty bars; the website shows a short notice. |

A licensed provider for production implements the same two-method interface (`resolve(isin)`,
`history(listing, start, end)`) and plugs into `provider_from_env()`.

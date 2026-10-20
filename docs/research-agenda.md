# Research agenda

The dataset exists to answer questions that matter for investors, regulators and economists. Each study
lives in `research/<study>/`, is reproducible from the published Parquet files, and states its limitations.

## 1. Do European insiders beat the market? (WS6)
- **Question:** abnormal returns after open-market purchases and sales by managers, by country, role
  (CEO/CFO versus board), trade size, cluster buys (several insiders of one issuer within a few days) and
  firm size.
- **Method:** market-model event study (CAR over [0,+5], [0,+20], [0,+60] trading days after publication,
  not after the trade, because publication is when outsiders can act), calendar-time portfolios as a
  robustness check, and multiple-testing awareness.
- **Literature:** Seyhun (1986, *Journal of Financial Economics*); Lakonishok and Lee (2001, *Review of
  Financial Studies*); Fidrmuc, Goergen and Renneboog (2006, *Journal of Finance*) for the UK; Betzer and
  Theissen (2009, *European Financial Management*) for Germany; Cohen, Malloy and Pomorski (2012, *Journal of
  Finance*) on routine versus opportunistic insiders.
- **Contribution:** a current, multi-country, open replication that anyone can re-run.

## 2. Flying under the radar: bunching below the 0.5% short disclosure threshold (WS7)
- **Question:** do position holders keep net short positions just below the publication threshold, and
  does it differ across authorities?
- **Method:** bunching estimation (Saez, 2010, *AEJ: Economic Policy*; Kleven, 2016, *Annual Review of
  Economics*) on the distribution of disclosed and, where available, historic positions.
- **Literature:** Jank, Roling and Smajlbegovic (2021, *Journal of Financial Economics*) document this
  behaviour with German data; this project can test it across Europe.

## 3. The Listing Act threshold change (WS7)
- **Question:** what happened to the volume and informativeness of PDMR notifications when Regulation (EU)
  2024/2809 raised the baseline threshold from EUR 5,000 to EUR 20,000, with national options between EUR
  10,000 and EUR 50,000?
- **Method:** difference-in-differences across jurisdictions with different threshold choices, plus
  bunching of yearly cumulative amounts below each national threshold.

## 4. Compliance: filing lags (WS7)
- **Question:** how often are notifications published later than the three-business-day deadline of MAR
  Article 19(1), by country, issuer size and role? Do late filings carry more or less information?
- An early signal: in the first two weeks of Swedish data ingested (October 2026), 18 of 306 records were
  published more than five calendar days after the transaction.

## 5. English publication and US attention (later)
- **Question:** did the SEC's March 2026 exemption, which depends on English-language publication of MAR
  filings, change market reactions to insider filings of dual-listed EEA issuers?

All results are research, not investment advice.

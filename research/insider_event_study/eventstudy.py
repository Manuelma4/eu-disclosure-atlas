"""Market-model event study and calendar-time portfolios (pure functions, no network).

Conventions (see README.md, "Method"):
- Trading calendar: the benchmark's trading days.
- Day 0: the first session in which an outsider could trade after publication: the publication day if the
  filing was published before the close (17:25 Stockholm time), otherwise the next trading day.
- Market model estimated by OLS over days [-250, -30], at least 120 returns.
- Returns from forward-filled adjusted closes (at most 10 stale days), so a thinly traded stock's multi-day
  move is booked on the day it trades again; a window is dropped if the stock has no price at its end.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

WINDOWS = {"[0,0]": (0, 0), "[0,+5]": (0, 5), "[0,+20]": (0, 20), "[0,+60]": (0, 60), "[+1,+20]": (1, 20),
           "[-20,-1]": (-20, -1)}
EST_START, EST_END, EST_MIN = -250, -30, 120
PATH = (-20, 60)
CLOSE_CUTOFF = pd.Timedelta(hours=17, minutes=25)
MAX_STALE = 10


@dataclass
class Panel:
    """Returns on a common trading calendar: `stocks[t, j]` for ticker j, `market[t]`."""

    dates: pd.DatetimeIndex
    tickers: list[str]
    stocks: np.ndarray
    market: np.ndarray
    traded_value: np.ndarray  # raw close x volume, for the size proxy
    has_price: np.ndarray  # a (possibly stale) price exists on that day

    @property
    def column(self) -> dict[str, int]:
        return {t: j for j, t in enumerate(self.tickers)}


def build_panel(prices: pd.DataFrame, benchmark: str) -> Panel:
    bench = prices[prices["ticker"] == benchmark].set_index("date")["adj_close"].sort_index()
    dates = pd.DatetimeIndex(bench.index)
    stock_prices = prices[prices["ticker"] != benchmark]
    adj = stock_prices.pivot_table(index="date", columns="ticker", values="adj_close").reindex(dates)
    value = (stock_prices.assign(v=stock_prices["raw_close"] * stock_prices["volume"])
             .pivot_table(index="date", columns="ticker", values="v").reindex(dates))
    filled = adj.ffill(limit=MAX_STALE)
    # Do not extend a series beyond its last real price (delisting or end of data).
    last_valid = adj.apply(lambda s: s.last_valid_index())
    for ticker, last in last_valid.items():
        if last is not None:
            filled.loc[filled.index > last, ticker] = np.nan
    returns = filled.pct_change(fill_method=None)
    return Panel(
        dates=dates,
        tickers=list(adj.columns),
        stocks=returns.to_numpy(),
        market=bench.pct_change().to_numpy(),
        traded_value=value.to_numpy(),
        has_price=filled.notna().to_numpy(),
    )


def day_zero(published_local: pd.Series, dates: pd.DatetimeIndex) -> np.ndarray:
    """Index into `dates` of day 0 for each publication timestamp (Stockholm local time); -1 if outside."""
    day = published_local.dt.normalize()
    after_close = (published_local - day) >= CLOSE_CUTOFF
    pos = dates.searchsorted(day.to_numpy(), side="left")
    pos = np.where(after_close.to_numpy() & (pos < len(dates)) & (dates[np.minimum(pos, len(dates) - 1)] == day),
                   pos + 1, pos)
    return np.where(pos < len(dates), pos, -1)


def market_model(r: np.ndarray, m: np.ndarray) -> tuple[float, float, float, int] | None:
    ok = ~(np.isnan(r) | np.isnan(m))
    n = int(ok.sum())
    if n < EST_MIN:
        return None
    x, y = m[ok], r[ok]
    beta, alpha = np.polyfit(x, y, 1)
    resid = y - alpha - beta * x
    return float(alpha), float(beta), float(np.sqrt(resid @ resid / (n - 2))), n


def abnormal_returns(events: pd.DataFrame, panel: Panel, model: str = "market_model") -> tuple[pd.DataFrame,
                                                                                                np.ndarray]:
    """Per-event CARs (one column per window) and the AR path over PATH (NaN where unavailable).

    `model` is "market_model" (OLS alpha and beta from the estimation window) or "market_adjusted"
    (alpha 0, beta 1: immune to extrapolating a pre-event run-up through alpha). Both require the same
    estimation window, whose residual volatility scales the BMP test. `events` needs `ticker` and `day0`.
    """
    col = panel.column
    lo, hi = PATH
    paths = np.full((len(events), hi - lo + 1), np.nan)
    rows = []
    for i, (ticker, d0) in enumerate(zip(events["ticker"], events["day0"], strict=True)):
        out = {"est_n": 0, "sigma": np.nan, "alpha": np.nan, "beta": np.nan, "size_value": np.nan}
        j = col.get(ticker)
        if j is None or d0 < 0 or d0 + EST_START < 1:
            rows.append(out)
            continue
        est = slice(d0 + EST_START, d0 + EST_END + 1)
        fit = market_model(panel.stocks[est, j], panel.market[est])
        if fit is None:
            rows.append(out)
            continue
        alpha, beta, sigma, n = fit
        if model == "market_adjusted":
            excess = panel.stocks[est, j] - panel.market[est]
            alpha, beta, sigma = 0.0, 1.0, float(np.nanstd(excess, ddof=1))
        out.update(est_n=n, sigma=sigma, alpha=alpha, beta=beta, size_value=np.nanmedian(panel.traded_value[est, j]))
        a, b = max(d0 + lo, 0), min(d0 + hi, len(panel.dates) - 1)
        ar = panel.stocks[a : b + 1, j] - (alpha + beta * panel.market[a : b + 1])
        paths[i, a - (d0 + lo) : b - (d0 + lo) + 1] = ar
        for name, (w0, w1) in WINDOWS.items():
            end = d0 + w1
            if end >= len(panel.dates) or not panel.has_price[end, j]:
                out[name] = np.nan  # window not complete (recent event, delisting or data gap)
                continue
            seg = paths[i, w0 - lo : w1 - lo + 1]
            out[name] = np.nansum(seg) if np.isfinite(seg).sum() >= 0.8 * len(seg) else np.nan
        rows.append(out)
    return pd.DataFrame(rows, index=events.index), paths


def summarize(car: pd.Series, sigma: pd.Series, length: int) -> dict[str, float]:
    """Mean CAR with the cross-sectional t and the BMP (Boehmer, Musumeci and Poulsen, 1991) standardized t."""
    ok = car.notna() & sigma.notna() & (sigma > 0)
    c, s = car[ok].to_numpy(), sigma[ok].to_numpy()
    n = len(c)
    if n < 2:
        return {"n": n, "mean": np.nan, "median": np.nan, "pos": np.nan, "t": np.nan, "t_bmp": np.nan,
                "p_bmp": np.nan}
    t = c.mean() / (c.std(ddof=1) / np.sqrt(n))
    scar = c / (s * np.sqrt(length))
    t_bmp = scar.mean() / (scar.std(ddof=1) / np.sqrt(n))
    return {
        "n": n,
        "mean": c.mean(),
        "median": float(np.median(c)),
        "pos": float((c > 0).mean()),
        "t": t,
        "t_bmp": t_bmp,
        "p_bmp": float(2 * stats.t.sf(abs(t_bmp), n - 1)),
    }


def benjamini_hochberg(p: pd.Series) -> pd.Series:
    """BH false-discovery-rate q-values (NaN p-values are kept as NaN)."""
    valid = p.dropna().sort_values()
    m = len(valid)
    if m == 0:
        return p * np.nan
    q = valid.to_numpy() * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(q[::-1])[::-1].clip(max=1.0)
    return pd.Series(q, index=valid.index).reindex(p.index)


def flag_clusters(events: pd.DataFrame, window: int = 5) -> pd.Series:
    """True if at least two distinct insiders of the issuer traded in the same direction with day 0 within
    the trailing `window` trading days (day 0 included). Uses only information public at day 0."""
    flags = pd.Series(False, index=events.index)
    for _, group in events.sort_values("day0").groupby(["isin", "nature"]):
        days = group["day0"].to_numpy()
        ids = group["pdmr_ids"].to_list()
        for k, idx in enumerate(group.index):
            first = np.searchsorted(days, days[k] - window + 1, side="left")
            insiders = set().union(*ids[first : k + 1])
            flags[idx] = len(insiders) >= 2
    return flags


def calendar_time(events: pd.DataFrame, panel: Panel, horizon: int, min_stocks: int = 1, entry: int = 0
                  ) -> pd.DataFrame:
    """Daily equal-weighted return of stocks held for `horizon` trading days from day `entry` after day 0
    (entry 0 includes the day-0 return; entry 1 buys at the day-0 close, which any outsider can do).

    Returns a frame indexed by date with columns `ret`, `mkt` and `n` (stocks held).
    """
    col = panel.column
    held = np.zeros_like(panel.stocks, dtype=bool)
    for ticker, d0 in zip(events["ticker"], events["day0"], strict=True):
        j = col.get(ticker)
        if j is not None and d0 >= 0:
            held[d0 + entry : d0 + entry + horizon, j] = True
    r = np.where(held & ~np.isnan(panel.stocks), panel.stocks, np.nan)
    n = np.isfinite(r).sum(axis=1)
    ret = np.full(len(n), np.nan)
    ret[n > 0] = np.nanmean(r[n > 0], axis=1)
    frame = pd.DataFrame({"ret": ret, "mkt": panel.market, "n": n}, index=panel.dates)
    return frame[(frame["n"] >= min_stocks) & frame["mkt"].notna()]


def universe_return(panel: Panel) -> np.ndarray:
    """Equal-weighted daily return of every stock in the panel: a small-cap, equal-weight control factor."""
    out = np.full(len(panel.dates), np.nan)
    has = np.isfinite(panel.stocks).any(axis=1)
    out[has] = np.nanmean(panel.stocks[has], axis=1)
    return out


def alpha_newey_west(y: pd.Series, x: pd.DataFrame | pd.Series, lags: int = 5) -> dict[str, float]:
    """OLS y = a + b'x with Newey-West standard errors; alpha in daily units, beta on the first factor."""
    import statsmodels.api as sm

    model = sm.OLS(y.to_numpy(), sm.add_constant(np.asarray(x))).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return {"alpha": float(model.params[0]), "t": float(model.tvalues[0]), "beta": float(model.params[1]),
            "days": int(len(y))}

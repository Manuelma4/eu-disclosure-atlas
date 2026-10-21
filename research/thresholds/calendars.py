"""Business-day calendars for the MAR Article 19 deadlines (Sweden and France).

MAR does not define "business day"; we use the working days of the member state whose authority receives and
publishes the notification: Monday to Friday minus national public holidays. Sweden also counts Midsummer Eve,
Christmas Eve and New Year's Eve as non-working days (banks and FI are closed). Holidays are generated from
fixed dates and Easter (anonymous Gregorian algorithm), so no extra dependency is needed.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def easter(year: int) -> date:
    """Western Easter Sunday (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holidays(country: str, year: int) -> list[date]:
    sunday = easter(year)
    if country == "SE":
        midsummer_eve = next(date(year, 6, d) for d in range(19, 26) if date(year, 6, d).weekday() == 4)
        return [
            date(year, 1, 1),
            date(year, 1, 6),
            sunday - timedelta(days=2),
            sunday + timedelta(days=1),
            date(year, 5, 1),
            sunday + timedelta(days=39),
            date(year, 6, 6),
            midsummer_eve,
            date(year, 12, 24),
            date(year, 12, 25),
            date(year, 12, 26),
            date(year, 12, 31),
        ]
    if country == "FR":
        return [
            date(year, 1, 1),
            sunday + timedelta(days=1),
            date(year, 5, 1),
            date(year, 5, 8),
            sunday + timedelta(days=39),
            sunday + timedelta(days=50),
            date(year, 7, 14),
            date(year, 8, 15),
            date(year, 11, 1),
            date(year, 11, 11),
            date(year, 12, 25),
        ]
    raise ValueError(f"no calendar for {country}")


def business_day_lag(trade: pd.Series, published: pd.Series, country: str) -> pd.Series:
    """Business days after the trade date up to and including the publication date (0 = same day).

    A publication on day T+3 (business days) meets a three-business-day deadline; T+4 misses it. A publication
    on a weekend or holiday counts as the next business day.
    """
    out = pd.Series(np.nan, index=trade.index)
    ok = trade.notna() & published.notna() & (published >= trade)
    if not ok.any():
        return out
    years = range(int(trade[ok].dt.year.min()), int(published[ok].dt.year.max()) + 2)
    hol = np.array([np.datetime64(d) for y in years for d in holidays(country, y)], dtype="datetime64[D]")
    t = trade[ok].to_numpy(dtype="datetime64[D]")
    p = published[ok].to_numpy(dtype="datetime64[D]")
    # Count business days in (t, p], with p rolled forward to a business day.
    p = np.busday_offset(p, 0, roll="forward", holidays=hol)
    out[ok] = np.busday_count(t + np.timedelta64(1, "D"), p + np.timedelta64(1, "D"), holidays=hol)
    return out

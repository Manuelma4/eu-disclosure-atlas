"""When to poll each regulator, and what "fresh" means for it.

Polling windows follow each authority's publication hours in its own time zone. Sources without an entry
get ``DEFAULT``. Thresholds feed the freshness asset checks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Regulator:
    timezone: str
    first_hour: int
    """First local hour of the polling window."""
    last_hour: int
    """Polling stops at this local hour, on the hour (e.g. 20 means the last poll is at 20:00)."""
    every_minutes: int
    weekdays: str = "1-5"
    """Cron day-of-week field for the business-hours polling."""
    max_staleness_hours: float = 96.0
    """Freshness check: the newest publication must be at most this old (covers weekends and a holiday)."""
    history_start: str = "2016-07-03"
    """First daily partition. MAR (Regulation (EU) No 596/2014) applies from 3 July 2016 (Article 39(2))."""

    def business_hours_crons(self) -> list[str]:
        """Cron expressions polling every ``every_minutes`` from first_hour:00 to last_hour:00 inclusive."""
        return [
            f"*/{self.every_minutes} {self.first_hour}-{self.last_hour - 1} * * {self.weekdays}",
            f"0 {self.last_hour} * * {self.weekdays}",
        ]


REGULATORS: dict[str, Regulator] = {
    # Finansinspektionen publishes PDMR notifications continuously on business days, often late in the evening;
    # the nightly settle run (see definitions.py) picks up anything published after 20:00.
    "SE": Regulator(timezone="Europe/Stockholm", first_hour=7, last_hour=20, every_minutes=5),
}

DEFAULT = Regulator(timezone="Europe/Brussels", first_hour=7, last_hour=20, every_minutes=15)


def regulator_for(jurisdiction: str) -> Regulator:
    return REGULATORS.get(jurisdiction.upper(), DEFAULT)

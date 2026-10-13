"""Part E — Multi-window, multi-burn-rate alerting (Google SRE Workbook, ch. 5).

Input: per-minute ``total`` and ``bad`` request counts for a 30-day window.

burn_rate(window) = error_ratio(window) / (1 - SLO)

A rule fires at minute i when BOTH its long and its short window (ending at i,
inclusive) burn at least ``burn_rate``. The long window says "this is
significant"; the short window says "it is still happening" (so the alert
resets quickly after recovery).

Tests: tests/test_alerting.py, then `make slo`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlertRule:
    name: str
    severity: str  # "page" or "ticket"
    long_min: int
    short_min: int
    burn_rate: float


# SRE Workbook defaults for a 30-day SLO window
DEFAULT_RULES: tuple[AlertRule, ...] = (
    AlertRule("fast_burn_2pct_1h", "page", 60, 5, 14.4),
    AlertRule("slow_burn_5pct_6h", "page", 360, 30, 6.0),
    AlertRule("budget_leak_10pct_3d", "ticket", 4320, 360, 1.0),
)


@dataclass(frozen=True)
class AlertEvent:
    rule: str
    severity: str
    start_min: int  # first minute the rule fired
    end_min: int  # first minute it stopped firing (exclusive)


class WindowSums:
    """O(1) sum of any window, via prefix sums. Build once, query many times.

    43,200 minutes x 6 windows: recomputing every window with sum() is far too slow.
    """

    def __init__(self, values: list[int]) -> None:
        self.values = values
        self.n = len(values)
        self.prefix = [0] * (self.n + 1)
        for i, val in enumerate(values):
            self.prefix[i + 1] = self.prefix[i] + val

    def sum(self, end_inclusive: int, length: int) -> int:
        if self.n == 0 or end_inclusive < 0 or length <= 0:
            return 0
        end_idx = min(end_inclusive, self.n - 1)
        start_idx = max(0, end_inclusive - length + 1)
        if start_idx > end_idx:
            return 0
        return self.prefix[end_idx + 1] - self.prefix[start_idx]


class BurnRateAlerter:
    def __init__(self, slo: float = 0.995, rules: tuple[AlertRule, ...] = DEFAULT_RULES) -> None:
        if not 0 < slo < 1:
            raise ValueError("slo must be in (0, 1)")
        self.slo = slo
        self.rules = rules

    def burn_rate(self, total: WindowSums, bad: WindowSums, end: int, length: int) -> float:
        tot = total.sum(end, length)
        if tot == 0:
            return 0.0
        b = bad.sum(end, length)
        return (b / tot) / (1.0 - self.slo)

    def evaluate(self, total: list[int], bad: list[int]) -> list[AlertEvent]:
        tot_ws = WindowSums(total)
        bad_ws = WindowSums(bad)
        n = len(total)
        events: list[AlertEvent] = []

        for rule in self.rules:
            firing = False
            start_min = 0
            for i in range(n):
                long_burn = self.burn_rate(tot_ws, bad_ws, i, rule.long_min)
                short_burn = self.burn_rate(tot_ws, bad_ws, i, rule.short_min)
                is_firing = (long_burn >= rule.burn_rate) and (short_burn >= rule.burn_rate)
                if is_firing and not firing:
                    firing = True
                    start_min = i
                elif not is_firing and firing:
                    firing = False
                    events.append(AlertEvent(rule.name, rule.severity, start_min, i))
            if firing:
                events.append(AlertEvent(rule.name, rule.severity, start_min, n))

        events.sort(key=lambda e: (e.start_min, e.end_min, e.rule))
        return events


def error_budget_report(total: list[int], bad: list[int], slo: float = 0.995) -> dict[str, float | bool]:
    req_sum = sum(total)
    allowed_bad = round((1.0 - slo) * req_sum, 1)
    bad_sum = sum(bad)
    avail = round(1.0 - (bad_sum / req_sum), 6) if req_sum > 0 else 1.0
    consumed = round(bad_sum / allowed_bad, 4) if allowed_bad > 0 else 0.0
    remaining = round(1.0 - consumed, 4)
    freeze = remaining <= 0

    return {
        "requests": req_sum,
        "allowed_bad": allowed_bad,
        "bad": bad_sum,
        "availability": avail,
        "budget_consumed": consumed,
        "budget_remaining": remaining,
        "freeze_releases": freeze,
    }


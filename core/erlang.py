"""Erlang C staffing helpers for queue FTE requirement calculations."""
from __future__ import annotations

import math


def erlang_c(traffic: float, agents: int) -> float:
    """
    Probability a caller waits (Erlang C).

    traffic: Erlangs in the interval (offered load).
    agents: staffed agent count (integer >= 1).
    """
    if agents <= 0:
        return 1.0
    if traffic <= 0:
        return 0.0
    if traffic >= agents:
        return 1.0

    sum_terms = 0.0
    for k in range(agents):
        sum_terms += traffic**k / math.factorial(k)

    last = (traffic**agents) / math.factorial(agents)
    last *= agents / (agents - traffic)
    return last / (sum_terms + last)


def service_level(
    traffic: float,
    agents: int,
    target_seconds: float,
    aht_seconds: float,
) -> float:
    """Fraction of callers answered within target_seconds (Erlang C model)."""
    if agents <= 0 or aht_seconds <= 0:
        return 0.0
    if traffic <= 0:
        return 1.0
    if agents <= traffic:
        return 0.0

    wait_prob = erlang_c(traffic, agents)
    exponent = -(agents - traffic) * (target_seconds / aht_seconds)
    return max(0.0, min(1.0, 1.0 - wait_prob * math.exp(exponent)))


def agents_required(
    traffic: float,
    *,
    sl_target: float = 0.80,
    target_seconds: float = 60.0,
    aht_seconds: float = 240.0,
    min_agents: int = 1,
    max_agents: int = 500,
) -> int:
    """Minimum integer agents to meet service level target for given traffic Erlangs."""
    if traffic <= 0 or aht_seconds <= 0:
        return min_agents

    start = max(min_agents, int(math.ceil(traffic)))
    for agents in range(start, max_agents + 1):
        if service_level(traffic, agents, target_seconds, aht_seconds) >= sl_target:
            return agents
    return max_agents


def workload_erlangs(offered: int, aht_seconds: float, interval_seconds: float) -> float:
    """Convert offered contacts and AHT into Erlangs for the interval."""
    if offered <= 0 or aht_seconds <= 0 or interval_seconds <= 0:
        return 0.0
    return (offered * aht_seconds) / interval_seconds


def required_fte(
    offered: int,
    aht_seconds: float,
    interval_seconds: float,
    *,
    sl_target: float = 0.80,
    target_seconds: float = 60.0,
    shrinkage: float = 0.0,
    min_coverage: float = 1.0,
) -> float:
    """
    Required staffed FTE for an interval (float), including shrinkage and minimum coverage.

    shrinkage: fraction of time agents are unavailable (0.30 = 30% shrinkage).
    """
    traffic = workload_erlangs(offered, aht_seconds, interval_seconds)
    if traffic <= 0:
        return min_coverage

    raw = float(
        agents_required(
            traffic,
            sl_target=sl_target,
            target_seconds=target_seconds,
            aht_seconds=aht_seconds,
        )
    )
    sl_required = max(raw, min_coverage)
    if shrinkage >= 1.0:
        return sl_required
    scheduled = sl_required / (1.0 - shrinkage)
    return max(scheduled, min_coverage)

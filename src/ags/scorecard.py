from datetime import timedelta

HORIZON = timedelta(weeks=1)


def effective_weights(calls: list[dict]) -> list[float]:
    """Weight each call by 1/k, k = same-commodity calls (itself included) whose 1-week windows overlap it.

    Consecutive calls share most of their price path, so counting each as an
    independent sample overstates confidence. Windows are [t, t + 1 week);
    returns weights in the order of `calls`. Each call is a dict with
    `commodity` and a timezone-aware `triggered_at`.
    """
    weights = []
    for a in calls:
        overlapping = sum(
            1
            for b in calls
            if b["commodity"] == a["commodity"] and abs(b["triggered_at"] - a["triggered_at"]) < HORIZON
        )
        weights.append(1 / overlapping)
    return weights


def effective_count(calls: list[dict]) -> float:
    return sum(effective_weights(calls))

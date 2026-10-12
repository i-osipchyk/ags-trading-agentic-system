from datetime import datetime, timedelta


def prior_day_aws_cost(ce_client, *, now: datetime):
    """Cost Explorer lags, so the digest reports the previous full day and labels it as such."""
    day = (now - timedelta(days=1)).date()

    def fetch() -> tuple[str, float]:
        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": day.isoformat(), "End": now.date().isoformat()},
            Granularity="DAILY", Metrics=["UnblendedCost"],
        )
        return day.isoformat(), float(response["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"])

    return fetch

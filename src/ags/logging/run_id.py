from datetime import datetime


def generate_run_id(commodity: str, trigger_timestamp: datetime) -> str:
    timestamp = trigger_timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{commodity}_{timestamp}"

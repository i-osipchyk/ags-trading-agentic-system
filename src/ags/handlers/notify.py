import html
import json


def handle(event: dict, *, telegram) -> dict:
    """CloudWatch alarm (via SNS) -> Telegram. Only transitions into ALARM are alerted."""
    sent = 0
    for record in event.get("Records", []):
        alarm = json.loads(record["Sns"]["Message"])
        if alarm.get("NewStateValue") != "ALARM":
            continue
        telegram.send(f"<b>ALARM {html.escape(alarm['AlarmName'])}</b>\n{html.escape(alarm.get('NewStateReason', ''))}")
        sent += 1
    return {"sent": sent}


def handler(event, context):
    from ags.handlers.entry import notify_handler

    return notify_handler(event, context)

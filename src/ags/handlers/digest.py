from ags.notify.digest import compose_digest


def handle(event: dict, *, storage, telegram, aws_cost, now) -> dict:
    telegram.send(compose_digest(storage, now=now, aws_cost=aws_cost))
    return {"sent": True}


def handler(event, context):
    from ags.handlers.entry import digest_handler

    return digest_handler(event, context)

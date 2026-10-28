from ags.notify.digest import compose_digest


def handle(event: dict, *, storage, telegram, now, manager_store: str = "manager") -> dict:
    telegram.send(compose_digest(storage, now=now, manager_store=manager_store))
    return {"sent": True}


def handler(event, context):
    from ags.handlers.entry import digest_handler

    return digest_handler(event, context)

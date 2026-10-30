from datetime import date

# Month -> stage, by commodity, for the regions get_weather covers for that commodity: the US crops are
# Northern-Hemisphere US calendars; sugar, coffee and cocoa follow their dominant producers (Brazil
# Center-South, Brazil, West Africa).
_CALENDARS: dict[str, dict[int, str]] = {
    "corn": {
        1: "dormant", 2: "dormant", 3: "dormant",
        4: "planting", 5: "planting",
        6: "vegetative",
        7: "flowering",
        8: "grain_fill",
        9: "harvest", 10: "harvest", 11: "harvest",
        12: "dormant",
    },
    "soybeans": {
        1: "dormant", 2: "dormant", 3: "dormant",
        4: "planting", 5: "planting",
        6: "vegetative", 7: "vegetative",
        8: "flowering",
        9: "harvest", 10: "harvest",
        11: "dormant", 12: "dormant",
    },
    "wheat": {
        # US winter wheat majority: fall-planted, overwinters dormant,
        # harvested early summer.
        1: "dormant", 2: "dormant",
        3: "green_up",
        4: "jointing",
        5: "flowering",
        6: "harvest", 7: "harvest",
        8: "dormant",
        9: "planting",
        10: "dormant", 11: "dormant", 12: "dormant",
    },
    "cotton": {
        1: "dormant", 2: "dormant", 3: "dormant",
        4: "planting", 5: "planting",
        6: "vegetative",
        7: "flowering",
        8: "boll_development",
        9: "harvest", 10: "harvest", 11: "harvest",
        12: "dormant",
    },
    "sugar": {
        # Brazil Center-South cane (the ICE No.11 price driver): crush April-November, regrowth otherwise.
        1: "vegetative", 2: "vegetative", 3: "vegetative",
        4: "harvest", 5: "harvest", 6: "harvest", 7: "harvest", 8: "harvest", 9: "harvest",
        10: "harvest", 11: "harvest",
        12: "vegetative",
    },
    "coffee": {
        # Brazil-centric (Southern Hemisphere) — seasons are shifted
        # relative to the US-centric crops above.
        1: "cherry_development", 2: "cherry_development", 3: "cherry_development",
        4: "pre_harvest",
        5: "harvest", 6: "harvest", 7: "harvest", 8: "harvest",
        9: "flowering", 10: "flowering",
        11: "fruit_set", 12: "fruit_set",
    },
    "cocoa": {
        # West Africa-centric — two harvest windows (main + mid crop).
        1: "harvest", 2: "harvest", 3: "harvest",
        4: "flowering",
        5: "harvest", 6: "harvest", 7: "harvest", 8: "harvest",
        9: "flowering",
        10: "harvest", 11: "harvest", 12: "harvest",
    },
}


def get_growing_calendar(commodity: str, as_of: date) -> dict:
    return {"stage": _CALENDARS[commodity][as_of.month]}

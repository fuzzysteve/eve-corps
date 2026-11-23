"""Fixed option lists for advert attributes: stored key -> display label."""

TIMEZONES = {
    "eu": "EUTZ",
    "us": "USTZ",
    "au": "AUTZ",
}

LANGUAGES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "es": "Spanish",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}

ACTIVITIES = {
    "pvp": "PvP",
    "pve": "PvE",
    "industry": "Industry",
    "mining": "Mining",
    "exploration": "Exploration",
    "fw": "Faction Warfare",
    "wormholes": "Wormholes",
}

SPACE = {
    "highsec": "High-sec",
    "lowsec": "Low-sec",
    "nullsec": "Null-sec",
    "wormhole": "Wormhole space",
}

# Multi-select attributes, by the Advert column name that stores them.
MULTI = {
    "timezones": TIMEZONES,
    "languages": LANGUAGES,
    "activities": ACTIVITIES,
    "space": SPACE,
}

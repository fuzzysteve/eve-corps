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

# How committed members are expected to be. Recruiter-set, single choice.
COMMITMENT = {
    "casual": "Casual",
    "regular": "Regular",
    "hardcore": "Hardcore",
}
COMMITMENT_HELP = {
    "casual": "No mandatory ops; play when you like.",
    "regular": "Some expected participation, e.g. weekly ops or home defence.",
    "hardcore": "Mandatory ops, activity requirements, strong commitment.",
}

# Corporations in faction warfare (verified against the SDE chrFactions table and
# ESI /fw/stats: the four empires plus the pirate factions).
FW_FACTIONS = {
    500001: "Caldari State",
    500002: "Minmatar Republic",
    500003: "Amarr Empire",
    500004: "Gallente Federation",
    500010: "Guristas Pirates",
    500011: "Angel Cartel",
}

# Member-count buckets: key -> (label, min, max inclusive or None).
SIZES = {
    "small": ("Under 10", 0, 9),
    "medium": ("10 to 50", 10, 50),
    "large": ("51 to 250", 51, 250),
    "huge": ("Over 250", 251, None),
}

# Maximum ISK tax filter options, in percent (ESI allows 0-100).
MAX_TAX = [0, 5, 10, 15, 25, 50]
# At or above this ISK tax rate, cards and corp pages flag it with a badge.
HIGH_TAX = 50

# Minimum corp age: key -> (label, days).
MIN_AGE = {
    "6m": ("6 months or more", 182),
    "1y": ("1 year or more", 365),
    "5y": ("5 years or more", 5 * 365),
}

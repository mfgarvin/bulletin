"""Explicit site-to-parish mappings for multi-site bulletins."""

# Parishes with genuine 24/7 perpetual adoration, verified by hand against
# their bulletins. The sanitizer normally flags `is_perpetual: true` with no
# listed hours as suspect; these are the real thing, so they're exempt and
# won't clutter the Issue Log every week.
VERIFIED_PERPETUAL_PARISHES: set[str] = {
    "21865",  # Queen of Heaven
    "st-martin-of-tours-maple-heights-oh",  # Saint Martin of Tours
    "sc-p",  # Saint Columbkille
    "our-lady-of-mount-carmel-wickliffe-oh",  # Our Lady of Mt. Carmel
    "0885",  # Sacred Heart of Jesus
    "1236",  # Holy Family
    "1608",  # Sacred Heart of Jesus (Wadsworth / Divine Mercy Chapel)
    "olg-m",  # Our Lady of Guadalupe
    "2492",  # Saint Charles Borromeo, Parma
}

# Parishes that should always be treated as single-site.
# When multiple sites are extracted:
# 1. Filters to sites matching the parish name (discards unrelated parishes)
# 2. Merges matching sites into one (combines Church + Chapel schedules)
SINGLE_SITE_PARISHES: set[str] = {
    # Add parish IDs here, e.g.:
    "ss-c",
    "5493",
    "1285",
    "0077",
    "0674",
    "0342",
    "0036",
    "our-lady-of-victory-tallmadge-oh",
    "0523",
    "st-matthew-akron-oh",
    "st-vitus-cleveland-oh",
    "29182",
    "0244",
    "nativity-of-blessed-virgin-mary-lorain-oh",
    "our-lady-of-lourdes-cleveland-oh",
    "sem-c",
    "st-mel-cleveland-oh",
    "1776",  # Saint Mark — shares a cluster schedule with St. Mel
    "sc-c",  # St. Casimir — bulletin also carries St. Stanislaus (its own row, 0242)
    "0069",
    "1548",
    "0138",
    "20812",
    "st-jerome-cleveland-oh",
    "0342",
}

# Sites to drop from a parish's extraction before any collapse happens.
#
# For a bulletin that lists a worship site belonging to a DIFFERENT parish's
# row. SINGLE_SITE_PARISHES cannot express this: its name filter keeps only the
# best-scoring sites, so it would also discard legitimate secondary sites (a
# chapel, a temporary worship space) whose names don't share distinctive words
# with the parish name.
#
# Each rule is {"match": substring, "unless": (substrings,)}. All matched
# case-insensitively against the extracted site name; a site is dropped when
# `match` is present and no `unless` term is. Ignored if it would drop every
# site.
#
# The `unless` guard is not optional bookkeeping. The extracted site name is
# free text the model writes fresh each run, and it does NOT name a site the
# same way twice — see studies/noise/signature_recurring.py. Always check a
# new rule against the site names a real bulletin actually produces.
SITE_EXCLUSIONS: dict[str, list[dict]] = {
    # The Cathedral bulletin lists the Oratory of the Immaculate Conception
    # with its own address and Saturday vigil. IC is its own parish with its
    # own ICKSP bulletin (immat-con-cle), which already publishes that Mass -
    # merging it here duplicated it onto the Cathedral's address.
    #
    # The guard matters more than the match. In 4 of 10 recorded runs the model
    # used the SAME name for the Cathedral's own temporary renovation chapel
    # ("The Oratory of the Immaculate Conception (Temporary Weekday Chapel)"),
    # which supplies 10 of the parish's 15 Masses. A bare substring match drops
    # those and leaves the parish with Sundays only.
    # The model splits the Oratory out as a site in only ~half of runs; in the
    # rest it copies the vigil inline into the Cathedral's own Mass list, note
    # reading "Sunday Vigil at Immaculate Conception" (usually without
    # "Oratory of the", and sometimes mentioning the Cathedral's own chapel in
    # passing - so notes need their own pattern and guard). `note_match` drops
    # a recurring Mass whose note names the excluded parish; `note_unless`
    # keeps the Cathedral's own Masses for the *feast* of the Immaculate
    # Conception (Dec 8), whose notes carry the same words.
    "1259": [
        {
            "match": "oratory of the immaculate conception",
            "unless": ("chapel", "temporary", "weekday", "renovation"),
            "note_match": "immaculate conception",
            "note_unless": ("solemnity", "feast", "holy day", "holyday"),
        },
    ],
}

# Format:
# "primary-parish-id": {
#     "pattern in extracted name": "target-parish-id",
# }
#
# Or, in other words:
# "bulletin-group-id": {
#    "pattern-to-search-for": "target parish id to change",
# }
# - Key is the bulletin_group_id (primary parish's ID)
# - Patterns are matched case-insensitively against extracted site names
# - First matching pattern wins

SITE_MAPPINGS: dict[str, dict[str, str]] = {
    # Our Lady Help of Christians (4 worship sites)
    "our-lady-help-of-christians-litchfield-oh": {
        "litchfield": "our-lady-help-of-christians-litchfield-oh",
        "lodi": "olhc-lodi",
        "nova": "olhc-nova",
        "seville": "olhc-seville",
    },
    "1071": {
	"holy trinity": "1071",
	"st. mary": "1071-MIC",
    },
    "0141": {
	"st. peter": "0141",
	"st. julie": "0141-JB",
    },
    "visitation-of-mary-parish-akron-oh": {
	"visitation": "visitation-of-mary-parish-akron-oh",
	"st. john": "visitation-of-mary-parish-akron-oh-sjb",
    },
    "saint-agnes-elyria-oh": {
	"agnes": "saint-agnes-elyria-oh",
	"mary": "saint-agnes-elyria-oh-ola",
    },
    "1905": {
	"st. patrick": "1905",
	"st. malachi": "1905-smo",
    },
    "st-vincent-de-paul-elyria-oh": {
	"vincent": "st-vincent-de-paul-elyria-oh",
	"cabrini": "st-vincent-de-paul-elyria-oh-sfxc",
    },
    "1806": {
	"robert": "1806",
	"john": "1806-sjc",
    },
    "1137": {
	"patrick": "1137",
	"vincent": "1137-svdp",
    },
    "1855": {
	"luke": "1855",
	"james": "1855-james",
	"clement": "1855-clem",
    },
    "0512": {
	"peace": "0512-peace",
	"andrew": "0512",
    },
    "amherst": {
	"joseph": "amherst",
	"nativity": "amherst-bvm",
    },
    "bearer": {
	"edward": "bearer",
	"lucy": "bearer-mission",
    },
    "shc": {
	"heart": "shc",
	"patrick": "shc-pat",
    },
    "0414": {
	"ann": "0414",
	"philomena": "0414-sp",
    },
    # The Cathedral (1259) is a single-row bulletin group: its main space plus a
    # temporary weekday-Mass chapel both belong in the 1259 row. Because the
    # group has one destination, the single-site collapse in main.py already
    # merges every extracted site into 1259, so these keys are belt-and-braces.
    # Immaculate Conception is NOT routed here anymore — it is its own enabled
    # Self-Hosted ICKSP oratory (institute-christ-king.org/cleveland-bulletins),
    # distinct from the Cathedral.
    "1259": {
	"cathedral": "1259",
	"chapel": "1259",
    },
    "0147": {
	"anthony": "0147",
	"teresa": "1786",
    },
}

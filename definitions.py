"""Explicit site-to-parish mappings for multi-site bulletins."""

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
    "0069",
    "1548",
    "0138",
    "20812",
    "st-jerome-cleveland-oh",
    "0342",
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
    "1259": {
	"cathedral": "1259",
	"immaculate": "immat-con-cle",
    },
    "0147": {
	"anthony": "0147",
	"teresa": "1786",
    },
}

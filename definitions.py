"""Explicit site-to-parish mappings for multi-site bulletins."""

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
    "33997": {
	"holy trinity": "33997",
	"st. mary": "33997-MIC",
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
    }
}

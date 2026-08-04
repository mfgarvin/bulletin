"""Compare official diocesan parish list with Notion database."""

import asyncio
import os
import re
from dataclasses import dataclass

from notion_client import AsyncClient


@dataclass
class OfficialParish:
    """Parish from the official diocesan directory."""
    parish_id: str
    name: str
    city: str | None = None


@dataclass
class NotionParish:
    """Parish from Notion database."""
    parish_id: str
    name: str
    enabled: bool
    city: str | None = None


# Official parish list extracted from CleCatDirTrimmed.pdf
# Format: (ID, Name, City)
OFFICIAL_PARISHES = [
    ("1101", "Cathedral of St. John the Evangelist", "Cleveland"),
    ("3202", "Assumption Parish", "Broadview Heights"),
    ("4219", "Blessed Trinity Parish", "Akron"),
    ("1316", "Blessed Trinity Parish", "Cleveland"),
    ("2122", "Communion of Saints Parish", "Cleveland Heights"),
    ("2303", "Divine Word Parish", "Kirtland"),
    ("2117", "Gesu Parish", "University Heights"),
    ("4318", "Guardian Angels Parish", "Copley"),
    ("2218", "Holy Angels Parish", "Chagrin Falls"),
    ("3214", "Holy Family Parish", "Parma"),
    ("4215", "Holy Family Parish", "Stow"),
    ("4132", "Holy Martyrs Parish", "Medina"),
    ("1425", "Holy Name Parish", "Cleveland"),
    ("1110", "Holy Redeemer Parish", "Cleveland"),
    ("2119", "Holy Rosary Parish", "Cleveland"),
    ("3301", "Holy Spirit Parish", "Avon Lake"),
    ("1427", "Holy Spirit Parish", "Garfield Heights"),
    ("3321", "Holy Trinity Parish", "Avon"),
    ("4304", "Immaculate Conception Parish", "Akron"),
    ("1113", "Immaculate Conception Parish", "Cleveland"),
    ("2304", "Immaculate Conception Parish", "Madison"),
    ("2328", "Immaculate Conception Parish", "Willoughby"),
    ("1410", "Immaculate Heart of Mary Parish", "Cleveland"),
    ("4208", "Immaculate Heart of Mary Parish", "Cuyahoga Falls"),
    ("1315", "Mary Queen of Peace Parish", "Cleveland"),
    ("3221", "Mary Queen of the Apostles Parish", "Brook Park"),
    ("4213", "Mother of Sorrows Parish", "Peninsula"),
    ("3311", "Nativity of the Blessed Virgin Mary Parish", "Lorain"),
    ("3334", "Nativity of the Blessed Virgin Mary Parish", "South Amherst"),
    ("4321", "Nativity of the Lord Jesus Parish", "Akron"),
    ("4124", "Our Lady Help of Christians Parish", "Litchfield"),
    ("1308", "Our Lady of Angels Parish", "Cleveland"),
    ("4123", "Our Lady of Grace Parish", "Hinckley"),
    ("2216", "Our Lady of Guadalupe Parish", "Macedonia"),
    ("1416", "Our Lady of Lourdes Parish", "Cleveland"),
    ("2312", "Our Lady of Mount Carmel Parish", "Wickliffe"),
    ("1213", "Our Lady of Mount Carmel Parish (West)", "Cleveland"),
    ("1418", "Our Lady of Peace Parish", "Cleveland"),
    ("2123", "Our Lady of the Lake Parish", "Euclid"),
    ("4216", "Our Lady of Victory Parish", "Tallmadge"),
    ("3337", "Our Lady Queen of Peace Parish", "Grafton"),
    ("4324", "Prince of Peace Parish", "Norton"),
    ("4319", "Queen of Heaven Parish", "Uniontown"),
    ("2211", "Resurrection of Our Lord Parish", "Solon"),
    ("3313", "Sacred Heart Chapel", "Lorain"),
    ("2124", "Sacred Heart of Jesus Parish", "South Euclid"),
    ("4131", "Sacred Heart of Jesus Parish", "Wadsworth"),
    ("3333", "Sacred Heart Parish", "Oberlin"),
    ("1222", "Sagrada Familia Parish", "Cleveland"),
    ("3102", "St. Adalbert Parish", "Berea"),
    ("1102", "St. Adalbert Parish", "Cleveland"),
    ("3323", "St. Agnes Parish", "Elyria"),
    ("4111", "St. Agnes Parish", "Orrville"),
    ("1103", "St. Agnes/Our Lady of Fatima Parish", "Cleveland"),
    ("3208", "St. Albert the Great Parish", "North Royalton"),
    ("1104", "St. Aloysius - St. Agatha Parish", "Cleveland"),
    ("3218", "St. Ambrose Parish", "Brunswick"),
    ("4320", "St. Andrew the Apostle Parish", "Norton"),
    ("3104", "St. Angela Merici Parish", "Fairview Park"),
    ("4112", "St. Anne Parish", "Rittman"),
    ("2321", "St. Anselm Parish", "Chesterland"),
    ("4202", "St. Anthony of Padua Parish", "Akron"),
    ("2302", "St. Anthony of Padua Parish", "Fairport Harbor"),
    ("3303", "St. Anthony of Padua Parish", "Lorain"),
    ("3209", "St. Anthony of Padua Parish", "Parma"),
    ("4312", "St. Augustine Parish", "Barberton"),
    ("1201", "St. Augustine Parish", "Cleveland"),
    ("1202", "St. Barbara Parish", "Cleveland"),
    ("2215", "St. Barnabas Parish", "Northfield"),
    ("3207", "St. Bartholomew Parish", "Middleburg Heights"),
    ("3201", "St. Basil the Great Parish", "Brecksville"),
    ("2305", "St. Bede the Venerable Parish", "Mentor"),
    ("3117", "St. Bernadette Parish", "Westlake"),
    ("4301", "St. Bernard Parish", "Akron"),
    ("1204", "St. Boniface Parish", "Cleveland"),
    ("3110", "St. Brendan Parish", "North Olmsted"),
    ("3210", "St. Bridget of Kildare Parish", "Parma"),
    ("1125", "St. Casimir Parish", "Cleveland"),  # Neff Road
    ("1106", "St. Casimir Parish", "Cleveland"),  # Sowinski Avenue
    ("3211", "St. Charles Borromeo Parish", "Parma"),
    ("3114", "St. Christopher Parish", "Rocky River"),
    ("2113", "St. Clare Parish", "Lyndhurst"),
    ("3111", "St. Clarence Parish", "North Olmsted"),
    ("3105", "St. Clement Parish", "Lakewood"),
    ("3217", "St. Colette Parish", "Brunswick"),
    ("1205", "St. Colman Parish", "Cleveland"),
    ("3212", "St. Columbkille Parish", "Parma"),
    ("2311", "St. Cyprian Parish", "Perry"),
    ("2114", "St. Dominic Parish", "Shaker Heights"),
    ("4101", "St. Edward Parish", "Ashland"),
    ("2325", "St. Edward Parish", "Parkman"),
    ("3336", "St. Elizabeth Ann Seton Parish", "Columbia Station"),
    ("1206", "St. Emeric Parish", "Cleveland"),
    ("4207", "St. Eugene Parish", "Cuyahoga Falls"),
    ("3339", "St. Frances Xavier Cabrini Parish", "Lorain"),
    ("4302", "St. Francis de Sales Parish", "Akron"),
    ("3213", "St. Francis de Sales Parish", "Parma"),
    ("2111", "St. Francis of Assisi Parish", "Gates Mills"),
    ("4127", "St. Francis Xavier Parish", "Medina"),
    ("2306", "St. Gabriel Parish", "Concord Township"),
    ("2324", "St. Helen Parish", "Newbury"),
    ("4303", "St. Hilary Parish", "Fairlawn"),
    ("1304", "St. Ignatius of Antioch Parish", "Cleveland"),
    ("3108", "St. James Parish", "Lakewood"),
    ("1114", "St. Jerome Parish", "Cleveland"),
    ("2204", "St. Joan of Arc Parish", "Chagrin Falls"),
    ("3216", "St. John Bosco Parish", "Parma Heights"),
    ("1208", "St. John Cantius Parish", "Cleveland"),
    ("1411", "St. John Nepomucene Parish", "Cleveland"),
    ("3219", "St. John Neumann Parish", "Strongsville"),
    ("2120", "St. John of the Cross Parish", "Euclid"),
    ("4305", "St. John the Baptist Parish", "Akron"),
    ("2307", "St. John Vianney Parish", "Mentor"),
    ("3320", "St. Joseph Parish", "Amherst"),
    ("3302", "St. Joseph Parish", "Avon Lake"),
    ("4209", "St. Joseph Parish", "Cuyahoga Falls"),
    ("3220", "St. Joseph Parish", "Strongsville"),
    ("3325", "St. Jude Parish", "Elyria"),
    ("3331", "St. Julie Billiart Parish", "North Ridgeville"),
    ("2301", "St. Justin Martyr Parish", "Eastlake"),
    ("3118", "St. Ladislas Parish", "Westlake"),
    ("1305", "St. Leo the Great Parish", "Cleveland"),
    ("3109", "St. Luke Parish", "Lakewood"),
    ("1306", "St. Mark Parish", "Cleveland"),
    ("2209", "St. Martin of Tours Parish", "Maple Heights"),
    ("4130", "St. Martin of Tours Parish", "Valley City"),
    ("2314", "St. Mary Magdalene Parish", "Willowick"),
    ("2308", "St. Mary of the Assumption Parish", "Mentor"),
    ("3113", "St. Mary of the Falls Parish", "Olmsted Falls"),
    ("3322", "St. Mary of the Immaculate Conception Parish", "Avon"),
    ("4114", "St. Mary of the Immaculate Conception Parish", "Wooster"),
    ("4306", "St. Mary Parish", "Akron"),
    ("2201", "St. Mary Parish", "Bedford"),
    ("3103", "St. Mary Parish", "Berea"),
    ("2320", "St. Mary Parish", "Chardon"),
    ("1118", "St. Mary Parish", "Cleveland"),
    ("3326", "St. Mary Parish", "Elyria"),
    ("4210", "St. Mary Parish", "Hudson"),
    ("3310", "St. Mary Parish", "Lorain"),
    ("2310", "St. Mary Parish", "Painesville"),
    ("4322", "St. Matthew Parish", "Akron"),
    ("3215", "St. Matthias Parish", "Parma"),
    ("1307", "St. Mel Parish", "Cleveland"),
    ("3206", "St. Michael Parish", "Independence"),
    ("1211", "St. Michael the Archangel Parish", "Cleveland"),
    ("2205", "St. Monica Parish", "Garfield Heights"),
    ("2313", "St. Noel Parish", "Willoughby Hills"),
    ("2112", "St. Paschal Baylon Parish", "Highland Heights"),
    ("2326", "St. Patrick Parish", "Thompson"),
    ("3335", "St. Patrick Parish", "Wellington"),
    ("1214", "St. Patrick Parish (Bridge Ave.)", "Cleveland"),
    ("1310", "St. Patrick Parish (Rocky River Dr.)", "Cleveland"),
    ("4307", "St. Paul Parish", "Akron"),
    ("1120", "St. Paul Parish", "Cleveland"),
    ("1121", "St. Peter Parish", "Cleveland"),
    ("3312", "St. Peter Parish", "Lorain"),
    ("4102", "St. Peter Parish", "Loudonville"),
    ("3332", "St. Peter Parish", "North Ridgeville"),
    ("3101", "St. Raphael Parish", "Bay Village"),
    ("3112", "St. Richard Parish", "North Olmsted"),
    ("2212", "St. Rita Parish", "Solon"),
    ("1216", "St. Rocco Parish", "Cleveland"),
    ("4310", "St. Sebastian Parish", "Akron"),
    ("1420", "St. Stanislaus Parish", "Cleveland"),
    ("1218", "St. Stephen Parish", "Cleveland"),
    ("4113", "St. Stephen Parish", "West Salem"),
    ("3316", "St. Teresa of Avila Parish", "Sheffield Village"),
    ("2207", "St. Therese Parish", "Garfield Heights"),
    ("1314", "St. Thomas More Parish", "Brooklyn"),
    ("3317", "St. Thomas the Apostle Parish", "Sheffield Lake"),
    ("4214", "St. Victor Parish", "Richfield"),
    ("4311", "St. Vincent de Paul Parish", "Akron"),
    ("1313", "St. Vincent de Paul Parish", "Cleveland"),
    ("3328", "St. Vincent de Paul Parish", "Elyria"),
    ("1124", "St. Vitus Parish", "Cleveland"),
    ("1219", "St. Wendelin Parish", "Cleveland"),
    ("2217", "SS. Cosmas & Damian Parish", "Twinsburg"),
    ("4323", "SS. Peter & Paul Parish", "Doylestown"),
    ("2206", "SS. Peter & Paul Parish", "Garfield Heights"),
    ("2121", "SS. Robert & William Parish", "Euclid"),
    ("3120", "Transfiguration Parish", "Lakewood"),
    ("4218", "Visitation of Mary Parish", "Akron"),
    # Multi-site worship locations (not main parishes, but listed separately in some cases)
    # These are sub-sites of Our Lady Help of Christians (4124)
    # ("olhc-lodi", "Our Lady Help of Christians in Lodi", "Lodi"),
    # ("olhc-nova", "Our Lady Help of Christians in Nova", "Nova"),
    # ("olhc-seville", "Our Lady Help of Christians in Seville", "Seville"),
    # St. Lucy Mission is part of St. Edward Parish (2325)
    ("2323", "St. Lucy Mission", "Middlefield"),
    # St. Andrew Kim Parish (Korean Pastoral Center)
    ("1221", "St. Andrew Kim Parish", "Cleveland"),
    # Shrine of Saint Elizabeth of Hungary (Pastoral Center)
    ("1428", "Shrine of Saint Elizabeth of Hungary", "Cleveland"),
]


async def fetch_notion_parishes(client: AsyncClient, database_id: str) -> list[NotionParish]:
    """Fetch all parishes from Notion database."""
    parishes: list[NotionParish] = []
    cursor: str | None = None

    while True:
        if cursor:
            response = await client.databases.query(
                database_id=database_id, start_cursor=cursor
            )
        else:
            response = await client.databases.query(database_id=database_id)

        for row in response["results"]:
            props = row["properties"]

            # Extract ParishID
            parish_id_prop = props.get("ParishID", {})
            parish_id_items = parish_id_prop.get("rich_text", [])
            parish_id = parish_id_items[0]["plain_text"] if parish_id_items else ""

            # Extract Name
            name_prop = props.get("Name", {})
            name_items = name_prop.get("title", [])
            name = name_items[0]["plain_text"] if name_items else ""

            # Extract Enable
            enable_prop = props.get("Enable", {})
            enabled = enable_prop.get("checkbox", False)

            # Extract City
            city_prop = props.get("City", {})
            city_items = city_prop.get("rich_text", [])
            city = city_items[0]["plain_text"] if city_items else None

            if parish_id:  # Only include entries with a ParishID
                parishes.append(NotionParish(
                    parish_id=parish_id,
                    name=name,
                    enabled=enabled,
                    city=city,
                ))

        if not response.get("has_more"):
            break
        cursor = response["next_cursor"]

    return parishes


def normalize_name(name: str) -> str:
    """Normalize parish name for comparison."""
    # Remove common variations
    name = name.lower()
    name = re.sub(r'\s+', ' ', name)  # Normalize whitespace
    name = name.replace("saint", "st.")
    name = name.replace("saints", "ss.")
    name = name.replace("&", "and")
    name = name.strip()
    return name


async def main() -> None:
    """Compare official parish list with Notion database."""
    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["PARISH_DB_ID"]
    client = AsyncClient(auth=api_key)

    print("Fetching parishes from Notion...")
    notion_parishes = await fetch_notion_parishes(client, database_id)
    print(f"Found {len(notion_parishes)} parishes in Notion\n")

    # Build lookup dicts
    official_by_id = {p[0]: OfficialParish(p[0], p[1], p[2]) for p in OFFICIAL_PARISHES}
    notion_by_id = {p.parish_id: p for p in notion_parishes}

    official_ids = set(official_by_id.keys())
    notion_ids = set(notion_by_id.keys())

    # Find differences
    missing_from_notion = official_ids - notion_ids
    extra_in_notion = notion_ids - official_ids
    in_both = official_ids & notion_ids

    # Report: Missing from Notion (in official list but not in database)
    print("=" * 70)
    print("MISSING FROM NOTION (in official diocese list, not in your database)")
    print("=" * 70)
    if missing_from_notion:
        for pid in sorted(missing_from_notion):
            p = official_by_id[pid]
            print(f"  [{pid}] {p.name} ({p.city})")
    else:
        print("  None - all official parishes are in Notion!")
    print()

    # Report: Extra in Notion (in database but not in official list)
    print("=" * 70)
    print("EXTRA IN NOTION (in your database, not in official diocese list)")
    print("=" * 70)
    if extra_in_notion:
        for pid in sorted(extra_in_notion):
            p = notion_by_id[pid]
            status = "enabled" if p.enabled else "disabled"
            print(f"  [{pid}] {p.name} ({p.city or 'no city'}) - {status}")
    else:
        print("  None - all Notion parishes are in official list!")
    print()

    # Report: Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Official diocese parishes: {len(official_ids)}")
    print(f"  Notion database parishes:  {len(notion_ids)}")
    print(f"  Matching:                  {len(in_both)}")
    print(f"  Missing from Notion:       {len(missing_from_notion)}")
    print(f"  Extra in Notion:           {len(extra_in_notion)}")
    print()

    # Enabled/disabled breakdown
    enabled_count = sum(1 for p in notion_parishes if p.enabled)
    disabled_count = len(notion_parishes) - enabled_count
    print(f"  Notion enabled:   {enabled_count}")
    print(f"  Notion disabled:  {disabled_count}")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # Assume env vars are already set
    asyncio.run(main())

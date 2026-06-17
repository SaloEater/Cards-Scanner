"""
Downloads team logos from ESPN CDN and saves them into images/<sport>/<snakecase_name>.png
so the Card-Scanner app picks them up automatically.

URL pattern: https://a.espncdn.com/i/teamlogos/<sport>/500/<short_name>.png

Edit the dicts below to add/remove teams or sports.
"""

import re
import ssl
import time
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------------------
# Team data: {short_espn_code: "Full Team Name"}
# ---------------------------------------------------------------------------

NFL_TEAMS = {
    "ari": "Arizona Cardinals",
    "atl": "Atlanta Falcons",
    "bal": "Baltimore Ravens",
    "buf": "Buffalo Bills",
    "car": "Carolina Panthers",
    "chi": "Chicago Bears",
    "cin": "Cincinnati Bengals",
    "cle": "Cleveland Browns",
    "dal": "Dallas Cowboys",
    "den": "Denver Broncos",
    "det": "Detroit Lions",
    "gb":  "Green Bay Packers",
    "hou": "Houston Texans",
    "ind": "Indianapolis Colts",
    "jax": "Jacksonville Jaguars",
    "kc":  "Kansas City Chiefs",
    "lv":  "Las Vegas Raiders",
    "lac": "Los Angeles Chargers",
    "lar": "Los Angeles Rams",
    "mia": "Miami Dolphins",
    "min": "Minnesota Vikings",
    "ne":  "New England Patriots",
    "no":  "New Orleans Saints",
    "nyg": "New York Giants",
    "nyj": "New York Jets",
    "phi": "Philadelphia Eagles",
    "pit": "Pittsburgh Steelers",
    "sf":  "San Francisco 49ers",
    "sea": "Seattle Seahawks",
    "tb":  "Tampa Bay Buccaneers",
    "ten": "Tennessee Titans",
    "wsh": "Washington Commanders",
}

MLB_TEAMS = {
    "ari": "Arizona Diamondbacks",
    "ath": "Athletics",
    "atl": "Atlanta Braves",
    "bal": "Baltimore Orioles",
    "bos": "Boston Red Sox",
    "chc": "Chicago Cubs",
    "chw": "Chicago White Sox",
    "cin": "Cincinnati Reds",
    "cle": "Cleveland Guardians",
    "col": "Colorado Rockies",
    "det": "Detroit Tigers",
    "hou": "Houston Astros",
    "kc":  "Kansas City Royals",
    "laa": "Los Angeles Angels",
    "lad": "Los Angeles Dodgers",
    "mia": "Miami Marlins",
    "mil": "Milwaukee Brewers",
    "min": "Minnesota Twins",
    "nym": "New York Mets",
    "nyy": "New York Yankees",
    "phi": "Philadelphia Phillies",
    "pit": "Pittsburgh Pirates",
    "sd":  "San Diego Padres",
    "sf":  "San Francisco Giants",
    "sea": "Seattle Mariners",
    "stl": "St. Louis Cardinals",
    "tb":  "Tampa Bay Rays",
    "tex": "Texas Rangers",
    "tor": "Toronto Blue Jays",
    "wsh": "Washington Nationals",
}

NBA_TEAMS = {
    "atl": "Atlanta Hawks",
    "bos": "Boston Celtics",
    "bkn": "Brooklyn Nets",
    "cha": "Charlotte Hornets",
    "chi": "Chicago Bulls",
    "cle": "Cleveland Cavaliers",
    "dal": "Dallas Mavericks",
    "den": "Denver Nuggets",
    "det": "Detroit Pistons",
    "gs": "Golden State Warriors",
    "hou": "Houston Rockets",
    "ind": "Indiana Pacers",
    "lac": "Los Angeles Clippers",
    "lal": "Los Angeles Lakers",
    "mem": "Memphis Grizzlies",
    "mia": "Miami Heat",
    "mil": "Milwaukee Bucks",
    "min": "Minnesota Timberwolves",
    "no":  "New Orleans Pelicans",
    "ny": "New York Knicks",
    "okc": "Oklahoma City Thunder",
    "orl": "Orlando Magic",
    "phx": "Phoenix Suns",
    "por": "Portland Trail Blazers",
    "phi": "Philadelphia 76ers",
    "sa": "San Antonio Spurs",
    "sac": "Sacramento Kings",
    "tor": "Toronto Raptors",
    "wsh": "Washington Wizards",
    "utah": "Utah Jazz",
}

NHL_TEAMS = {
    "ana": "Anaheim Ducks",
    "ari": "Arizona Coyotes",
    "bos": "Boston Bruins",
    "buf": "Buffalo Sabres",
    "car": "Carolina Hurricanes",
    "cbj": "Columbus Blue Jackets",
    "chi": "Chicago Blackhawks",
    "col": "Colorado Avalanche",
    "cgy": "Calgary Flames",
    "dal": "Dallas Stars",
    "det": "Detroit Red Wings",
    "fla": "Florida Panthers",
    "edm": "Edmonton Oilers",
    "la": "Los Angeles Kings",
    "min": "Minnesota Wild",
    "mtl": "Montreal Canadiens",
    "nsh": "Nashville Predators",
    "nj": "New Jersey Devils",
    "nyi": "New York Islanders",
    "nyr": "New York Rangers",
    "ott": "Ottawa Senators",
    "phi": "Philadelphia Flyers",
    "pit": "Pittsburgh Penguins",
    "sea": "Seattle Kraken",
    "sj": "San Jose Sharks",
    "stl": "St. Louis Blues",
    "tb": "Tampa Bay Lightning",
    "tor": "Toronto Maple Leafs",
    "van": "Vancouver Canucks",
    "vgk": "Vegas Golden Knights",
    "wsh": "Washington Capitals",
    "utah": "Utah Mammoth",
    "wpg": "Winnipeg Jets",
}

SOCCER_TEAMS = {
    "111": "Juventus",
    "382": "Manchester United",
    "257": "Rangers FC",
    "103": "AC Milan",
    "363": "Chelsea FC",
    "132": "FC Bayern Munchen",
    "364": "Liverpool FC",
    "83": "FC Barcelona",
    "176": "Olympique de Marselle",
    "110": "FC Internazionale Milano",
    "86": "Real Madrid FC",
    "160": "Paris Saint-Germain",
    "2790": "FC Salzburg",
    "256": "Celtic FC",
    "367": "Tottenham Hotspur",
    "1068": "Atletico de Madrid",
    "124": "Borussia Dortmund"
}

# ---------------------------------------------------------------------------
# Sport registry: {espn_sport_code: team_dict}
# ---------------------------------------------------------------------------

SPORTS = {
    "nfl": NFL_TEAMS,
    "mlb": MLB_TEAMS,
    "nba": NBA_TEAMS,
    "nhl": NHL_TEAMS,
    "soccer": SOCCER_TEAMS,
}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path(__file__).parent / "images"
ESPN_URL = "https://a.espncdn.com/i/teamlogos/{sport}/500/{short}.png"
DELAY_SECONDS = 0.15   # polite pause between requests
TIMEOUT = 10

# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def download_logos(force: bool = False) -> None:
    total = sum(len(teams) for teams in SPORTS.values())
    done = skipped = failed = 0

    for sport, teams in SPORTS.items():
        sport_dir = OUTPUT_DIR / sport
        sport_dir.mkdir(parents=True, exist_ok=True)

        for short, full_name in teams.items():
            dest = sport_dir / f"{_slug(full_name)}.png"

            if dest.exists() and not force:
                skipped += 1
                print(f"  skip  {sport}/{dest.name}")
                continue

            url = ESPN_URL.format(sport=sport, short=short)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
                    dest.write_bytes(resp.read())
                print(f"  ok    {sport}/{dest.name}  ({url})")
                done += 1
            except Exception as exc:
                print(f"  FAIL  {sport}/{short} -> {full_name}: {exc}")
                failed += 1

            time.sleep(DELAY_SECONDS)

    print(f"\nDone: {done} downloaded, {skipped} skipped, {failed} failed  (total {total})")


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    if force:
        print("Force mode: re-downloading existing files\n")
    download_logos(force=force)

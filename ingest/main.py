"""Fetch Calgary newsroom RSS feeds and tag each item with a map location.

Neighborhood names come from data/communities.geojson. Landmark names come from
data/landmarks.json. A street address is looked up in the local Calgary street
index and then Nominatim, and kept when the point falls inside the address
quadrant. An address wins over a landmark or a
community named elsewhere in the article. Otherwise each article gets one pin:
the landmark point, or a point inside a community name that survives the name
guards. A bare quadrant mention is used only when nothing more specific matches.
Landmark ids named in the title or summary are stored on every article, in
order of first appearance, even when the pin came from an address or a community.
"""

import html
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

import certifi
import feedparser
from shapely.geometry import Point, shape

from streets import StreetIndex, load_street_index

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
COMMUNITIES_PATH = DATA_DIR / "communities.geojson"
QUADRANT_PATH = DATA_DIR / "quadrants.geojson"
LANDMARKS_PATH = DATA_DIR / "landmarks.json"
PUBLIC_LANDMARKS_PATH = ROOT / "web" / "public" / "landmarks.json"
ARTICLES_PATH = DATA_DIR / "articles.json"
PIN_FIELDS = ("lat", "lng", "neighborhood", "quadrant", "location_method")
GEOCODE_CACHE_PATH = DATA_DIR / "geocode-cache.json"
# Earlier feeds win when the same link appears in more than one.
FEEDS = (
    ("police", "https://newsroom.calgary.ca/tagfeed/en/tags/police"),
    ("fire", "https://newsroom.calgary.ca/tagfeed/en/tags/Fire"),
    ("city", "https://newsroom.calgary.ca/tagfeed/en/tags/transportation,city__news"),
    ("feature", "https://newsroom.calgary.ca/tagfeed/en/tags/feature"),
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "yyc-signal/1.0 (Calgary police newsroom map)"
MAX_AGE_DAYS = 30
# left, top, right, bottom. A hint, not a hard clip; community polygons decide.
CALGARY_VIEWBOX = "-114.4,51.25,-113.8,50.84"

CODED_DISTRICT = re.compile(r"\d{2}[A-Z]")
ORDINALS = (
    "First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth"
)
STREET_TYPES = (
    "Boulevard|Avenue|Crescent|Circle|Street|Drive|Trail|Court|Close|"
    "Place|Grove|Point|Green|Hills|Hill|Park|Road|Lane|Link|Rise|Gate|"
    "Way|Blvd|Cres|Ave|Dr|Rd|Ct|Ln|Pl|St"
)
NAME_STOP = (
    "and|at|of|the|on|in|a|an|located|near|outside|inside|block|to|from|for"
)
NAME_WORD = rf"(?!(?:{NAME_STOP})\b)[A-Za-z][A-Za-z']*"
STREET_NAME = rf"(?:\d+|{ORDINALS}|{NAME_WORD}(?:\s+{NAME_WORD}){{0,3}})"
QUADRANT_SUFFIX = r"(?:N|S)\.?\s*(?:E|W)\.?"
QUADRANT_TAIL = rf"\.?\s*,?\s*({QUADRANT_SUFFIX})(?!\w)"
BLOCK_ADDRESS = re.compile(
    rf"\b(\d+)(?:\s*-\s*(\d+))?\s+block\s+of\s+({STREET_NAME})\s+"
    rf"({STREET_TYPES}){QUADRANT_TAIL}",
    re.IGNORECASE,
)
CIVIC_ADDRESS = re.compile(
    rf"\b(?:#\s*[A-Za-z0-9]+\s*,\s*)?(\d+)\s+({STREET_NAME})\s+"
    rf"({STREET_TYPES}){QUADRANT_TAIL}",
    re.IGNORECASE,
)
INTERSECTION = re.compile(
    rf"\b({STREET_NAME})\s+({STREET_TYPES})\.?\s+and\s+"
    rf"({STREET_NAME})\s+({STREET_TYPES}){QUADRANT_TAIL}",
    re.IGNORECASE,
)
SOUTH_OF = re.compile(
    rf"\b([A-Za-z]+)\s+(Trail)\.?,?\s+south\s+of\s+"
    rf"(\d+)\s+({STREET_TYPES}){QUADRANT_TAIL}",
    re.IGNORECASE,
)
DEERFOOT_AVENUE_S = re.compile(
    r"\b(\d+)\s+Avenue\s+S\b(?!\.?\s*(?:E|W)\b)",
    re.IGNORECASE,
)
TYPE_WORDS = {
    "ave": "Avenue",
    "st": "Street",
    "dr": "Drive",
    "blvd": "Boulevard",
    "rd": "Road",
    "cres": "Crescent",
    "ct": "Court",
    "ln": "Lane",
    "pl": "Place",
}
RANKS = re.compile(
    r"(?:insp|sgt|cst|const|det|constable|sergeant|inspector|mr|mrs|ms|dr|chief)",
    re.IGNORECASE,
)
FACILITY_NEXT = re.compile(
    r"\s+(?:Inn|Hotel|Motel|Centre|Medical|Hospital|Plaza|Mall)\b",
    re.IGNORECASE,
)
LOCATION_CONTEXT = re.compile(
    r"(?:in|near|at|around|of)\s+(?:the\s+)?$",
    re.IGNORECASE,
)
COMMUNITY_BEFORE = re.compile(
    r"(?:community|neighbourhood|neighborhood)\s+(?:of\s+)?(?:the\s+)?$",
    re.IGNORECASE,
)
COMMUNITY_AFTER = re.compile(
    r"\s+(?:community|neighbourhood|neighborhood)\b",
    re.IGNORECASE,
)
NOT_GIVEN_NAMES = {
    "a", "an", "the", "in", "of", "at", "near", "on", "from", "and", "after",
    "before", "outside", "inside", "calgary", "police", "service", "community",
    "north", "south", "east", "west", "northeast", "northwest", "southeast",
    "southwest", "avenue", "street", "drive", "trail",
}
QUADRANT_MENTION = re.compile(
    r"\b(?:north\s*-?\s*east|north\s*-?\s*west|south\s*-?\s*east|south\s*-?\s*west)\b"
    rf"|(?<!\w){QUADRANT_SUFFIX}(?!\w)",
    re.IGNORECASE,
)
QUADRANT_CODES = {
    "ne": "NE",
    "nw": "NW",
    "se": "SE",
    "sw": "SW",
    "northeast": "NE",
    "northwest": "NW",
    "southeast": "SE",
    "southwest": "SW",
}
APOSTROPHES = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u02bc": "'",
    "`": "'",
    "\u00b4": "'",
})


class Place:
    def __init__(self, phrase, kind, lat, lng, neighborhood, quadrant, point, landmark_id=None):
        self.phrase = phrase
        self.kind = kind
        self.lat = lat
        self.lng = lng
        self.neighborhood = neighborhood
        self.quadrant = quadrant
        self.point = point
        self.landmark_id = landmark_id
        self.pattern = compile_phrase(phrase)


def compile_phrase(phrase):
    body = r"\b" + re.escape(phrase) + r"\b"
    # "The Bow" is the tower. Bare "Bow" is not indexed, and the two-word name
    # must not claim "the Bow River", "the Bow Valley", or "the Bow Trail".
    if phrase.casefold() == "the bow":
        body += r"(?!\s+(?:river|valley|trail)\b)"
    return re.compile(body, re.IGNORECASE)


def normalize(text):
    return text.translate(APOSTROPHES)


def plain_text(value):
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = html.unescape(normalize(text))
    return re.sub(r"\s+", " ", text).strip()


def clean_phrase(text):
    return re.sub(r"\s+", " ", normalize(text)).strip(" ,-/")


def acceptable_landmark_phrase(phrase):
    if re.fullmatch(r"[A-Z]{3}", phrase):
        return True
    return len(phrase) >= 4


def phrase_variants(part):
    phrase = clean_phrase(part)
    if not phrase:
        return []
    found = [phrase]
    stripped = re.sub(r"^(?:the|cf)\s+", "", phrase, count=1, flags=re.IGNORECASE).strip()
    if stripped and stripped.casefold() != phrase.casefold():
        found.append(stripped)
    return found


def landmark_phrases(name):
    parts = [name]
    parts.extend(re.findall(r"\(([^)]*)\)", name))
    remainder = re.sub(r"\([^)]*\)", " ", name)
    parts.extend(re.split(r"\s*(?:/|—|–|,)\s*", remainder))
    phrases = []
    seen = set()
    for part in parts:
        for phrase in phrase_variants(part):
            key = phrase.casefold()
            if key in seen or not acceptable_landmark_phrase(phrase):
                continue
            seen.add(key)
            phrases.append(phrase)
    return phrases


def match_phrases(landmark):
    """Name and aliases, plus a leading The or CF removed from either."""
    phrases = []
    seen = set()
    for part in [landmark["name"], *landmark.get("aliases", [])]:
        for phrase in phrase_variants(part):
            key = phrase.casefold()
            if key in seen or not acceptable_landmark_phrase(phrase):
                continue
            seen.add(key)
            phrases.append(phrase)
    return phrases


def validate_landmarks(landmarks):
    """Fail on a repeated id, or on a name or alias claimed by two landmarks."""
    ids = {}
    claimed = {}
    for landmark in landmarks:
        name = landmark.get("name")
        landmark_id = landmark.get("id")
        if not isinstance(landmark_id, str) or not landmark_id:
            raise RuntimeError(f"landmark {name!r} is missing a slug id")
        if landmark_id in ids:
            raise RuntimeError(
                f"duplicate landmark id {landmark_id!r} "
                f"({ids[landmark_id]!r} and {name!r})"
            )
        ids[landmark_id] = name
        aliases = landmark.get("aliases", [])
        if not isinstance(aliases, list):
            raise RuntimeError(f"landmark {landmark_id!r} aliases must be a list")
        for phrase in match_phrases(landmark):
            key = phrase.casefold()
            owner = claimed.get(key)
            if owner is not None and owner != landmark_id:
                raise RuntimeError(
                    f"landmark phrase {phrase!r} belongs to {owner!r} and {landmark_id!r}"
                )
            claimed[key] = landmark_id
    return set(ids)


def load_landmarks():
    catalog = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
    validate_landmarks(catalog["landmarks"])
    return catalog


def neighborhood_phrases(name):
    if CODED_DISTRICT.fullmatch(name):
        return []
    phrases = [name]
    if "/" in name:
        for part in name.split("/"):
            part = part.strip()
            if len(part) >= 5:
                phrases.append(part)
    # The official name is "DOWNTOWN EAST VILLAGE". Releases say "East Village".
    if name == "DOWNTOWN EAST VILLAGE":
        phrases.append("East Village")
    return phrases


def load_places():
    collection = json.loads(COMMUNITIES_PATH.read_text(encoding="utf-8"))
    communities = []
    places = []
    for feature in collection["features"]:
        properties = feature["properties"]
        name = properties["NAME"]
        quadrant = properties["quadrant"]
        geometry = shape(feature["geometry"])
        communities.append((geometry, name, quadrant))
        point = geometry.representative_point()
        lat = round(point.y, 5)
        lng = round(point.x, 5)
        for phrase in neighborhood_phrases(name):
            places.append(Place(phrase, "neighborhood", lat, lng, name, quadrant, None))

    landmarks = load_landmarks()
    for landmark in landmarks["landmarks"]:
        lat = landmark["lat"]
        lng = landmark["lng"]
        point = Point(lng, lat)
        for phrase in match_phrases(landmark):
            places.append(
                Place(phrase, "landmark", lat, lng, None, None, point, landmark["id"])
            )
    return places, communities


def load_quadrants():
    collection = json.loads(QUADRANT_PATH.read_text(encoding="utf-8"))
    return {
        feature["properties"]["quadrant"]: shape(feature["geometry"])
        for feature in collection["features"]
    }


def community_at(point, communities):
    for geometry, name, quadrant in communities:
        if geometry.covers(point):
            return name, quadrant
    return None, None


class Address:
    def __init__(self, start, quadrant, queries, local):
        self.start = start
        self.quadrant = quadrant
        self.queries = queries
        self.local = local


def quadrant_code(text):
    compact = re.sub(r"[^A-Za-z]", "", text).casefold()
    return QUADRANT_CODES[compact]


def canonical_type(street_type):
    key = street_type.rstrip(".").casefold()
    if key in TYPE_WORDS:
        return TYPE_WORDS[key]
    return street_type[:1].upper() + street_type[1:].casefold()


def canonical_name(name):
    parts = []
    for part in name.split():
        if part.isdigit():
            parts.append(part)
        else:
            parts.append(part[:1].upper() + part[1:].casefold())
    return " ".join(parts)


def street_line(name, street_type, quadrant):
    return f"{canonical_name(name)} {canonical_type(street_type)} {quadrant}"


def civic_from_block(start, end):
    first = int(start)
    if first == 0 and end:
        return int(end)
    return first


def cross_street_query(block_number, name, street_type, quadrant):
    kind = street_type.casefold().rstrip(".")
    if kind in {"ave", "avenue"}:
        other = "Street"
    elif kind in {"st", "street"}:
        other = "Avenue"
    else:
        return None
    if re.fullmatch(rf"\d+|{ORDINALS}", name, re.IGNORECASE) is None:
        return None
    cross = int(block_number) // 100
    if cross < 1:
        return None
    return f"{street_line(name, street_type, quadrant)} and {cross} {other} {quadrant}"


def local_street(name, street_type):
    return canonical_name(name), canonical_type(street_type)


def address_from_block(match):
    quadrant = quadrant_code(match.group(5))
    civic = civic_from_block(match.group(1), match.group(2))
    name = match.group(3)
    street_type = match.group(4)
    queries = [f"{civic} {street_line(name, street_type, quadrant)}"]
    cross = cross_street_query(civic, name, street_type, quadrant)
    if cross:
        queries.append(cross)
    street_name, kind = local_street(name, street_type)
    local = {"kind": "block", "house": civic, "name": street_name, "type": kind}
    return Address(match.start(), quadrant, queries, local)


def address_from_civic(match):
    quadrant = quadrant_code(match.group(4))
    query = f"{match.group(1)} {street_line(match.group(2), match.group(3), quadrant)}"
    street_name, kind = local_street(match.group(2), match.group(3))
    local = {
        "kind": "civic",
        "house": int(match.group(1)),
        "name": street_name,
        "type": kind,
    }
    return Address(match.start(), quadrant, [query], local)


def address_from_intersection(match):
    quadrant = quadrant_code(match.group(5))
    left = street_line(match.group(1), match.group(2), quadrant)
    right = street_line(match.group(3), match.group(4), quadrant)
    local = {
        "kind": "intersection",
        "left": local_street(match.group(1), match.group(2)),
        "right": local_street(match.group(3), match.group(4)),
    }
    return Address(match.start(), quadrant, [f"{left} and {right}"], local)


def address_from_south_of(match):
    quadrant = quadrant_code(match.group(5))
    road = f"{match.group(1)} {canonical_type(match.group(2))}"
    cross = street_line(match.group(3), match.group(4), quadrant)
    local = {
        "kind": "intersection",
        "left": local_street(match.group(1), match.group(2)),
        "right": local_street(match.group(3), match.group(4)),
    }
    return Address(match.start(), quadrant, [f"{road} and {cross}"], local)


def address_from_deerfoot(text, match):
    if re.search(r"\bDeerfoot\s+Trail\b", text, re.IGNORECASE) is None:
        return None
    # Deerfoot runs east of the Centre Street / Macleod divider, so "17 Avenue S"
    # at that exit is southeast.
    query = f"Deerfoot Trail and {match.group(1)} Avenue SE"
    local = {
        "kind": "intersection",
        "left": ("Deerfoot", "Trail"),
        "right": (match.group(1), "Avenue"),
    }
    return Address(match.start(), "SE", [query], local)


def first_address(text):
    if not text:
        return None
    found = []
    for pattern, build in (
        (BLOCK_ADDRESS, address_from_block),
        (CIVIC_ADDRESS, address_from_civic),
        (INTERSECTION, address_from_intersection),
        (SOUTH_OF, address_from_south_of),
    ):
        match = pattern.search(text)
        if match:
            found.append(build(match))
    deerfoot = DEERFOOT_AVENUE_S.search(text)
    if deerfoot:
        parsed = address_from_deerfoot(text, deerfoot)
        if parsed:
            found.append(parsed)
    if not found:
        return None
    return min(found, key=lambda address: address.start)


def mentioned_quadrant(text):
    if not text:
        return None
    match = QUADRANT_MENTION.search(text)
    if match is None:
        return None
    return quadrant_code(match.group(0))


def preceded_by_person(text, match):
    prefix = text[:match.start()]
    if re.search(r"[.!?]\s*$", prefix):
        return False
    words = re.findall(r"[A-Za-z]+", prefix)
    if not words:
        return False
    previous = words[-1]
    if RANKS.fullmatch(previous):
        return True
    if previous.casefold() in NOT_GIVEN_NAMES:
        return False
    return previous[:1].isupper() and previous[1:].islower()


def followed_by_facility(text, match):
    return FACILITY_NEXT.match(text[match.end():]) is not None


def has_location_context(text, match):
    prefix = text[:match.start()]
    if LOCATION_CONTEXT.search(prefix) or COMMUNITY_BEFORE.search(prefix):
        return True
    return COMMUNITY_AFTER.match(text[match.end():]) is not None


def matching_neighborhoods(title, summary, places):
    found = []
    for source_rank, text in ((0, title), (1, summary)):
        if not text:
            continue
        for place in places:
            if place.kind != "neighborhood":
                continue
            match = place.pattern.search(text)
            if match:
                found.append((source_rank, place, match, text))
    return found


def best_neighborhood(title, summary, places):
    found = matching_neighborhoods(title, summary, places)
    names = {place.neighborhood for _, place, _, _ in found}
    # A list of communities is not one place. Count the raw hits, including
    # one-word names that the context rule will later refuse, so a photo
    # enforcement list still stays untagged.
    if len(names) >= 4:
        return None
    best = None
    best_key = None
    for source_rank, place, match, text in found:
        if followed_by_facility(text, match) or preceded_by_person(text, match):
            continue
        if len(place.phrase.split()) == 1 and not has_location_context(text, match):
            continue
        key = (source_rank, -len(place.phrase))
        if best_key is None or key < best_key:
            best_key = key
            best = place
    return best


def choose_landmark_spans(text, places):
    """Non-overlapping landmark hits. Longest spans win, then the earlier one."""
    if not text:
        return []
    spans = []
    for place in places:
        if place.kind != "landmark":
            continue
        for match in place.pattern.finditer(text):
            spans.append((match.start(), match.end(), place))
    spans.sort(key=lambda item: (item[0] - item[1], item[0]))
    chosen = []
    occupied = []
    for start, end, place in spans:
        if any(start < right and end > left for left, right in occupied):
            continue
        occupied.append((start, end))
        chosen.append((start, end, place))
    return chosen


def blank_landmarks(text, places):
    """Longest landmark spans win and are replaced with spaces.

    A shorter name, including a community inside the landmark, cannot match
    the text that was consumed.
    """
    chosen = choose_landmark_spans(text, places)
    if not chosen:
        return text or "", None
    chars = list(text)
    for start, end, _place in chosen:
        for index in range(start, end):
            chars[index] = " "
    best = min(chosen, key=lambda item: -(item[1] - item[0]))
    return "".join(chars), best[2]


def landmark_ids(title, summary, places):
    """Ids in order of first appearance. Title matches come before the summary."""
    found = []
    seen = set()
    for text in (title, summary):
        chosen = choose_landmark_spans(text, places)
        for _start, _end, place in sorted(chosen, key=lambda item: item[0]):
            landmark_id = place.landmark_id
            if not landmark_id or landmark_id in seen:
                continue
            seen.add(landmark_id)
            found.append(landmark_id)
    return found


def landmarks_then_blank(title, summary, places):
    best = None
    best_key = None
    blanked = []
    for source_rank, text in ((0, title), (1, summary)):
        leftover, place = blank_landmarks(text, places)
        blanked.append(leftover)
        if place is None:
            continue
        key = (source_rank, -len(place.phrase))
        if best_key is None or key < best_key:
            best_key = key
            best = place
    while len(blanked) < 2:
        blanked.append("")
    return best, blanked[0], blanked[1]


def unpack_geocode(found):
    if isinstance(found, dict):
        return found["lng"], found["lat"], found.get("display_name")
    if len(found) == 3:
        return found
    return found[0], found[1], None


def number_in_result(query, display_name):
    """Reject a long-road hit that dropped the house or block number.

    Avenues, streets, and trails run across many communities, so a point
    anywhere on them can be the wrong place. A local street is kept. A test
    geocoder that omits a display name is trusted.
    """
    if display_name is None:
        return True
    body = query.split(",")[0]
    number = re.match(r"(\d+)\b", body)
    if number is None:
        return True
    if re.search(rf"\b{re.escape(number.group(1))}\b", display_name):
        return True
    street_type = re.search(rf"\b({STREET_TYPES})\b", body, re.IGNORECASE)
    if street_type is None:
        return False
    kind = street_type.group(1).casefold().rstrip(".")
    return kind not in {"avenue", "ave", "street", "st", "trail"}


def placed_point(point, communities, quadrant, quadrants):
    """Keep the community that contains the point when the quadrant polygon does.

    A community such as Beltline is labeled with its larger side, so the
    community label is not the address quadrant. The stored quadrant is the
    suffix from the article.
    """
    name, _majority = community_at(point, communities)
    region = None if quadrants is None else quadrants.get(quadrant)
    if name is None or region is None or not region.covers(point):
        return None
    return round(point.y, 5), round(point.x, 5), name, quadrant


def place_address(address, geocode, communities, streets=None, quadrants=None):
    if streets is not None:
        for point in streets.candidates(address):
            placed = placed_point(point, communities, address.quadrant, quadrants)
            if placed is not None:
                return placed
    if geocode is None:
        return None
    for query in address.queries:
        full = f"{query}, Calgary, Alberta"
        found = geocode(full)
        if not found or not number_in_result(full, unpack_geocode(found)[2]):
            continue
        lng, lat, _display = unpack_geocode(found)
        placed = placed_point(Point(lng, lat), communities, address.quadrant, quadrants)
        if placed is not None:
            return placed
    return None


def locate(title, summary, places, communities, geocode=None, streets=None, quadrants=None):
    title_text = plain_text(title)
    summary_text = plain_text(summary)
    ids = landmark_ids(title_text, summary_text, places)
    address = first_address(title_text) or first_address(summary_text)
    if address is not None:
        placed = place_address(address, geocode, communities, streets, quadrants)
        if placed is not None:
            return (*placed, ids)
        return None, None, None, address.quadrant, ids

    landmark, title_left, summary_left = landmarks_then_blank(
        title_text, summary_text, places
    )
    if landmark is not None:
        neighborhood, quadrant = community_at(landmark.point, communities)
        return landmark.lat, landmark.lng, neighborhood, quadrant, ids
    neighborhood_place = best_neighborhood(title_left, summary_left, places)
    if neighborhood_place is not None:
        return (
            neighborhood_place.lat,
            neighborhood_place.lng,
            neighborhood_place.neighborhood,
            neighborhood_place.quadrant,
            ids,
        )
    for text in (title_text, summary_text):
        quadrant = mentioned_quadrant(text)
        if quadrant is not None:
            return None, None, None, quadrant, ids
    return None, None, None, None, ids


def expect(title, places, communities, neighborhood, quadrant, lng=None, lat=None, summary="", geocode=None, streets=None, quadrants=None):
    found_lat, found_lng, found_name, found_quadrant = locate(
        title, summary, places, communities, geocode, streets, quadrants
    )[:4]
    if (found_name, found_quadrant) != (neighborhood, quadrant):
        raise RuntimeError(
            f"{title!r} tagged {found_name} {found_quadrant}, "
            f"expected {neighborhood} {quadrant}"
        )
    if lng is None:
        if found_lat is None or found_lng is None:
            raise RuntimeError(f"{title!r} is missing coordinates")
        return
    if found_lat is None or abs(found_lat - lat) > 1e-4 or abs(found_lng - lng) > 1e-4:
        raise RuntimeError(
            f"{title!r} pinned {(found_lng, found_lat)}, expected {(lng, lat)}"
        )


def expect_quadrant(title, summary, places, communities, quadrant, geocode=None, streets=None, quadrants=None):
    found = locate(title, summary, places, communities, geocode, streets, quadrants)[:4]
    expected = (None, None, None, quadrant)
    if found != expected:
        raise RuntimeError(f"{title!r} tagged {found}, expected {expected}")


def expect_untagged(title, places, communities, summary="", geocode=None, streets=None, quadrants=None):
    found = locate(title, summary, places, communities, geocode, streets, quadrants)[:4]
    if any(value is not None for value in found):
        raise RuntimeError(f"{title!r} should not be tagged, got {found}")


def self_check(places, communities, quadrants):
    expect("Crash in Beltline", places, communities, "BELTLINE", "SW")
    expect(
        "Parking at Chinook Centre",
        places,
        communities,
        "MEADOWLARK PARK",
        "SW",
        lng=-114.07389,
        lat=50.99833,
    )
    expect(
        "Alarm sounded at The Bow",
        places,
        communities,
        "DOWNTOWN COMMERCIAL CORE",
        "SW",
        lng=-114.06222,
        lat=51.04778,
    )
    expect(
        "Lecture at the University of Calgary",
        places,
        communities,
        "UNIVERSITY OF CALGARY",
        "NW",
        lng=-114.13306,
        lat=51.0775,
    )
    expect(
        "Paths closed at Prince's Island Park",
        places,
        communities,
        "EAU CLAIRE",
        "SW",
        lng=-114.0675,
        lat=51.055,
    )
    expect("Stabbing in Falconridge", places, communities, "FALCONRIDGE", "NE")
    expect(
        "Search in the community of Falconridge",
        places,
        communities,
        "FALCONRIDGE",
        "NE",
    )
    expect_quadrant(
        "Stabbing in Falconridge in the 200 block of Falwood Way N.E.",
        "",
        places,
        communities,
        "NE",
    )
    expect_quadrant(
        "Garage break-in in the 1700 block of 35 Avenue S.W.",
        "",
        places,
        communities,
        "SW",
    )
    expect_quadrant(
        "Called to the 500 block of Seventh Avenue S.E.",
        "",
        places,
        communities,
        "SE",
    )
    expect_quadrant("Shooting in northeast Calgary", "", places, communities, "NE")
    expect_quadrant(
        "Crash in Beltline",
        "The garage was in the 1700 block of 35 Avenue S.W.",
        places,
        communities,
        "SW",
        geocode=lambda query: None,
    )
    sw_geometry, sw_name, _sw_quadrant = next(
        (geometry, name, quadrant)
        for geometry, name, quadrant in communities
        if quadrant == "SW" and name != "BELTLINE"
    )
    sw_point = sw_geometry.representative_point()

    def avenue_from_summary(query):
        if "35 Avenue SW" in query:
            return sw_point.x, sw_point.y
        return None

    expect(
        "Crash in Beltline",
        places,
        communities,
        sw_name,
        "SW",
        lng=sw_point.x,
        lat=sw_point.y,
        summary="The garage was in the 1700 block of 35 Avenue S.W.",
        geocode=avenue_from_summary,
        quadrants=quadrants,
    )

    def avenue_geocode(query):
        if "35 Avenue SW" in query:
            return sw_point.x, sw_point.y
        return None

    expect(
        "Garage break-in in the 1700 block of 35 Avenue S.W.",
        places,
        communities,
        *community_at(sw_point, communities),
        lng=sw_point.x,
        lat=sw_point.y,
        geocode=avenue_geocode,
        quadrants=quadrants,
    )
    foothills = next(place for place in places if place.phrase == "Foothills Medical Centre")
    foothills_name, foothills_quadrant = community_at(foothills.point, communities)
    if foothills_quadrant != "NW" or foothills_name == "FOOTHILLS":
        raise RuntimeError(
            f"Foothills Medical Centre landed in {foothills_name} {foothills_quadrant}"
        )
    expect(
        "Last seen at Foothills Medical Centre",
        places,
        communities,
        foothills_name,
        foothills_quadrant,
        lng=foothills.lng,
        lat=foothills.lat,
    )
    expect_quadrant(
        "Last seen at Foothills Medical Centre at 1403 29 Street N.W.",
        "",
        places,
        communities,
        "NW",
    )
    other_nw_geometry, other_nw_name, _other_nw_quadrant = next(
        (geometry, name, quadrant)
        for geometry, name, quadrant in communities
        if quadrant == "NW" and name != foothills_name
    )
    other_nw_point = other_nw_geometry.representative_point()

    def hospital_address(query):
        if "29 Street NW" in query:
            return other_nw_point.x, other_nw_point.y
        return None

    expect(
        "Last seen at Foothills Medical Centre at 1403 29 Street N.W.",
        places,
        communities,
        other_nw_name,
        "NW",
        lng=other_nw_point.x,
        lat=other_nw_point.y,
        geocode=hospital_address,
        quadrants=quadrants,
    )
    expect_quadrant(
        "Traffic stop outside of the Red Carpet Inn, located at 4635 16 Ave. N.W.",
        "",
        places,
        communities,
        "NW",
    )
    expect(
        "Stabbing in the community of East Village",
        places,
        communities,
        "DOWNTOWN EAST VILLAGE",
        "SE",
    )
    expect_untagged(
        "Photo radar in Albert Park Auburn Bay Bowness Brentwood and Bridlewood",
        places,
        communities,
    )
    expect_untagged("outside of the Red Carpet Inn", places, communities)
    expect_untagged("Meeting at the Foothills Inn", places, communities)
    expect_untagged("Const. Patterson attended the briefing", places, communities)
    expect_untagged(
        "Insp. Robert Patterson spoke about the inspection",
        places,
        communities,
    )
    ne_geometry, ne_name, _ne_quadrant = next(
        (geometry, name, quadrant)
        for geometry, name, quadrant in communities
        if quadrant == "NE" and name != "SADDLE RIDGE"
    )
    ne_point = ne_geometry.representative_point()
    saddle_summary = "Police were called to the 2016 block of 25 Avenue N.E."
    expect_quadrant(
        "Man arrested in Saddle Ridge",
        saddle_summary,
        places,
        communities,
        "NE",
        geocode=lambda query: None,
    )

    def saddle_address(query):
        if "25 Avenue NE" in query:
            return ne_point.x, ne_point.y
        return None

    expect(
        "Man arrested in Saddle Ridge",
        places,
        communities,
        ne_name,
        "NE",
        lng=ne_point.x,
        lat=ne_point.y,
        summary=saddle_summary,
        geocode=saddle_address,
        quadrants=quadrants,
    )
    check_local_streets(places, communities, quadrants)
    check_straddling_quadrant(places, communities, quadrants)
    expect_untagged("District 01B reported a delay", places, communities)
    expect_untagged("Crews searched Bow River", places, communities)
    expect_untagged("A kayaker was rescued from the Bow River", places, communities)


def check_straddling_quadrant(places, communities, quadrants):
    beltline = next(
        geometry for geometry, name, _quadrant in communities if name == "BELTLINE"
    )
    southeast = beltline.intersection(quadrants["SE"])
    southwest = beltline.intersection(quadrants["SW"])
    if southeast.is_empty or southwest.is_empty:
        raise RuntimeError("Beltline should cross the quadrant divider")
    se_point = southeast.representative_point()
    sw_point = southwest.representative_point()
    streets = StreetIndex(lines={}, houses={"15|AV|SE": [[210, se_point.y, se_point.x]]})
    expect(
        "Walking in the 200 block of 15 Avenue S.E.",
        places,
        communities,
        "BELTLINE",
        "SE",
        lng=se_point.x,
        lat=se_point.y,
        streets=streets,
        quadrants=quadrants,
    )
    wrong_side = StreetIndex(lines={}, houses={"15|AV|SE": [[210, sw_point.y, sw_point.x]]})
    expect_quadrant(
        "Walking in the 200 block of 15 Avenue S.E.",
        "",
        places,
        communities,
        "SE",
        streets=wrong_side,
        quadrants=quadrants,
    )


def check_local_streets(places, communities, quadrants):
    geometry = next(
        geometry for geometry, name, _quadrant in communities if name == "BELTLINE"
    )
    point = geometry.intersection(quadrants["SW"]).representative_point()
    lng = round(point.x, 6)
    lat = round(point.y, 6)
    other = next(
        geometry.representative_point()
        for geometry, name, quadrant in communities
        if quadrant == "SW" and name != "BELTLINE"
    )
    streets = StreetIndex(
        lines={
            "5|AV|SW": [[[lng - 0.01, lat], [lng + 0.01, lat]]],
            "3|ST|SW": [[[lng, lat - 0.01], [lng, lat + 0.01]]],
        },
        houses={
            "35|AV|SW": [[1720, lat, lng], [1760, lat, lng]],
            # 3939 is the same side of the street as 3949. 503 is the opposite
            # side of 500 and must not win over 506.
            "17|AV|SW": [
                [3939, lat, lng],
                [503, other.y, other.x],
                [506, lat, lng],
            ],
        },
    )
    name, quadrant = community_at(Point(lng, lat), communities)
    expect(
        "Collision at Fifth Avenue and Third Street S.W.",
        places,
        communities,
        name,
        quadrant,
        lng=lng,
        lat=lat,
        streets=streets,
        quadrants=quadrants,
    )
    expect(
        "Garage break-in in the 1700 block of 35 Avenue S.W.",
        places,
        communities,
        name,
        quadrant,
        lng=lng,
        lat=lat,
        streets=streets,
        quadrants=quadrants,
    )
    expect(
        "Alarm at 3949 17 Avenue S.W.",
        places,
        communities,
        name,
        quadrant,
        lng=lng,
        lat=lat,
        streets=streets,
        quadrants=quadrants,
    )
    expect(
        "Alarm at 500 17 Avenue S.W.",
        places,
        communities,
        name,
        quadrant,
        lng=lng,
        lat=lat,
        streets=streets,
        quadrants=quadrants,
    )


def fetch_feed(url):
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    handler = urllib.request.HTTPSHandler(context=ssl_context)
    feed = feedparser.parse(url, handlers=[handler])
    if not feed.entries:
        detail = getattr(feed, "bozo_exception", None) or feed.get("status")
        raise RuntimeError(f"no entries from {url}: {detail}")
    return feed


def articles_from(
    feed,
    places,
    communities,
    geocode=None,
    streets=None,
    quadrants=None,
    source=None,
    known_links=None,
):
    """Tag feed entries. known_links, when passed, is updated with new links."""
    skip = known_links if known_links is not None else set()
    articles = []
    for entry in feed.entries:
        link = entry.get("link") or None
        if link and link in skip:
            continue
        title = plain_text(entry.get("title"))
        summary = plain_text(entry.get("summary"))
        lat, lng, neighborhood, quadrant, landmarks = locate(
            title, summary, places, communities, geocode, streets, quadrants
        )
        articles.append(
            {
                "title": title,
                "summary": summary,
                "link": link,
                "published": entry.get("published") or None,
                "source": source,
                "lat": lat,
                "lng": lng,
                "neighborhood": neighborhood,
                "quadrant": quadrant,
                "landmarks": landmarks,
            }
        )
        if link:
            skip.add(link)
    return articles


def published_timestamp(article):
    published = article.get("published")
    if not published:
        return 0
    try:
        return parsedate_to_datetime(published).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0


def article_key(article):
    return article.get("link") or article.get("title")


def merge_articles(groups):
    """Keep the first copy of each link. groups are already in source priority order."""
    seen = set()
    merged = []
    for articles in groups:
        for article in articles:
            key = article_key(article)
            if key in seen:
                continue
            seen.add(key)
            merged.append(article)
    merged.sort(key=published_timestamp, reverse=True)
    return merged


def within_max_age(article, now):
    """Keep an article unless its published date is older than MAX_AGE_DAYS.

    A missing or unparsed date stays, so a bad timestamp cannot delete a row.
    """
    published = article.get("published")
    if not published:
        return True
    try:
        published_at = parsedate_to_datetime(published).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return True
    return published_at >= now - MAX_AGE_DAYS * 24 * 60 * 60


def merge_saved(saved, fetched, now=None):
    """Keep the saved copy of each link, append new links, then drop expired items."""
    if now is None:
        now = time.time()
    seen = set()
    merged = []
    for article in list(saved) + list(fetched):
        key = article_key(article)
        if key in seen:
            continue
        seen.add(key)
        merged.append(article)
    current = [article for article in merged if within_max_age(article, now)]
    current.sort(key=published_timestamp, reverse=True)
    return current


def load_saved_articles(path=ARTICLES_PATH):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def combine_feeds(saved, groups, failures, feed_count, now=None):
    """Merge feeds that returned items. None means every feed failed."""
    if len(failures) == feed_count:
        return None
    return merge_saved(saved, merge_articles(groups), now=now)


def articles_text(articles):
    return json.dumps(articles, indent=2, ensure_ascii=False) + "\n"


def store_articles(articles, path):
    """Write articles.json. Return False when the serialized set is unchanged."""
    payload = articles_text(articles)
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return False
    path.write_text(payload, encoding="utf-8")
    return True


def validate_article_landmarks(articles, known_ids):
    for article in articles:
        landmarks = article.get("landmarks")
        title = article.get("title")
        if not isinstance(landmarks, list):
            raise RuntimeError(f"{title!r} landmarks must be a list")
        for landmark_id in landmarks:
            if landmark_id not in known_ids:
                raise RuntimeError(f"unknown landmark id {landmark_id!r} on {title!r}")


def publish_landmarks():
    PUBLIC_LANDMARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_LANDMARKS_PATH.write_text(
        LANDMARKS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def write_articles(articles, path=ARTICLES_PATH):
    validate_article_landmarks(articles, validate_landmarks(load_landmarks()["landmarks"]))
    wrote = store_articles(articles, path)
    if path == ARTICLES_PATH:
        if not wrote:
            print("articles.json unchanged")
        publish_landmarks()
    return wrote


def persist_merged(articles, path=ARTICLES_PATH):
    """Write a merged feed. None means every feed failed and the file stays as it is."""
    if articles is None:
        return False
    write_articles(articles, path)
    return True


def assign_location(article, located):
    lat, lng, neighborhood, quadrant, landmarks = located
    article["lat"] = lat
    article["lng"] = lng
    article["neighborhood"] = neighborhood
    article["quadrant"] = quadrant
    ordered = {}
    for key, value in article.items():
        if key == "landmarks":
            continue
        ordered[key] = value
        if key == "quadrant":
            ordered["landmarks"] = landmarks
    if "landmarks" not in ordered:
        ordered["landmarks"] = landmarks
    article.clear()
    article.update(ordered)


def apply_locations(articles, places, communities, geocode=None, streets=None, quadrants=None):
    for article in articles:
        assign_location(
            article,
            locate(
                article["title"],
                article.get("summary"),
                places,
                communities,
                geocode,
                streets,
                quadrants,
            ),
        )
    return articles


def location_counts(articles):
    pinned = sum(article["lat"] is not None for article in articles)
    quadrant_only = sum(
        article["lat"] is None and article["quadrant"] is not None
        for article in articles
    )
    untagged = sum(
        article["lat"] is None and article["quadrant"] is None
        for article in articles
    )
    return pinned, quadrant_only, untagged


def make_geocoder(cache_path=GEOCODE_CACHE_PATH, sleep_seconds=1.0):
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        cache = {}
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    def store(query, value):
        cache[query] = value
        cache_path.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def geocode(query):
        if query in cache:
            found = lookup_cached_geocode(cache[query])
            if found is not False:
                return found
        params = urllib.parse.urlencode(
            {
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "ca",
                "viewbox": CALGARY_VIEWBOX,
            }
        )
        request = urllib.request.Request(
            f"{NOMINATIM_URL}?{params}",
            headers={"User-Agent": NOMINATIM_USER_AGENT},
        )
        time.sleep(sleep_seconds)
        try:
            with urllib.request.urlopen(request, context=ssl_context, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(5)
            with urllib.request.urlopen(request, context=ssl_context, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        if not payload:
            store(query, None)
            return None
        lat = round(float(payload[0]["lat"]), 5)
        lng = round(float(payload[0]["lon"]), 5)
        display_name = payload[0].get("display_name") or ""
        store(query, {"lat": lat, "lng": lng, "display_name": display_name})
        return lng, lat, display_name

    return geocode


def lookup_cached_geocode(entry):
    """Return a stored point, None for a stored miss, or False when the row must be fetched.

    A lat/lng row is a hit even without display_name, so a fresh checkout does not
    send that address to Nominatim again.
    """
    if entry is None:
        return None
    if isinstance(entry, dict) and "lat" in entry and "lng" in entry:
        return entry["lng"], entry["lat"], entry.get("display_name")
    return False


def cached_geocoder():
    """Read the saved Nominatim cache and do not call the network."""
    if GEOCODE_CACHE_PATH.exists():
        cache = json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8"))
    else:
        cache = {}

    def geocode(query):
        hit = cache.get(query)
        if not isinstance(hit, dict) or "display_name" not in hit:
            return None
        return hit["lng"], hit["lat"], hit["display_name"]

    return geocode


def pin_fields(article):
    return {field: article.get(field) for field in PIN_FIELDS}


def retag_saved():
    """Re-tag data/articles.json in place. Stop if a pin field moved."""
    places, communities = load_places()
    quadrants = load_quadrants()
    self_check(places, communities, quadrants)
    articles = json.loads(ARTICLES_PATH.read_text(encoding="utf-8"))
    before = [pin_fields(article) for article in articles]
    apply_locations(
        articles,
        places,
        communities,
        cached_geocoder(),
        load_street_index(),
        quadrants,
    )
    diffs = []
    for article, previous in zip(articles, before):
        current = pin_fields(article)
        if current != previous:
            diffs.append((article.get("title"), previous, current))
    if diffs:
        for title, previous, current in diffs:
            print(f"PIN CHANGED: {title}")
            for field in PIN_FIELDS:
                if previous[field] != current[field]:
                    print(f"  {field}: {previous[field]!r} -> {current[field]!r}")
        raise SystemExit(1)
    write_articles(articles)
    filled = sum(bool(article["landmarks"]) for article in articles)
    print(f"Re-tagged {len(articles)} articles")
    print(f"{filled} articles gained a non-empty landmarks list")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "retag":
        retag_saved()
        return
    places, communities = load_places()
    quadrants = load_quadrants()
    self_check(places, communities, quadrants)
    saved = load_saved_articles()
    known_links = {article["link"] for article in saved if article.get("link")}
    geocode = make_geocoder()
    streets = load_street_index()
    groups = []
    failures = []
    for source, url in FEEDS:
        try:
            feed = fetch_feed(url)
        except Exception as error:
            print(f"FEED FAILED {source}: {error}")
            failures.append(source)
            continue
        groups.append(
            articles_from(
                feed,
                places,
                communities,
                geocode,
                streets,
                quadrants,
                source,
                known_links,
            )
        )
    articles = combine_feeds(saved, groups, failures, len(FEEDS))
    if not persist_merged(articles):
        print("All feeds failed; leaving articles.json unchanged")
        raise SystemExit(1)
    pinned, quadrant_only, untagged = location_counts(articles)
    print(f"Fetched {len(articles)} articles")
    print(f"Pinned {pinned}, quadrant only {quadrant_only}, untagged {untagged}")


if __name__ == "__main__":
    main()

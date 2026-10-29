"""Local Calgary street index built from the city's open data.

Street Centreline (4dx8-rtm5) supplies intersection lines. Parcel Address
(s8b3-j88p) supplies house numbers and hundred-blocks. Both are already WGS84.
The index is tried before Nominatim.
"""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi
from shapely.geometry import LineString, Point

ROOT = Path(__file__).resolve().parents[1]
STREET_INDEX_PATH = ROOT / "data" / "street-index.json"
USER_AGENT = "yyc-signal/1.0 (Calgary police newsroom map)"
PAGE_SIZE = 50000
CENTRELINE_RESOURCE = "4dx8-rtm5"
ADDRESS_RESOURCE = "s8b3-j88p"

# Calgary stores numbered streets as digits and street types as two letters.
ORDINALS = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
}
TYPE_CODES = {
    "avenue": "AV",
    "ave": "AV",
    "street": "ST",
    "st": "ST",
    "drive": "DR",
    "dr": "DR",
    "boulevard": "BV",
    "blvd": "BV",
    "trail": "TR",
    "road": "RD",
    "rd": "RD",
    "way": "WY",
    "crescent": "CR",
    "cres": "CR",
    "place": "PL",
    "pl": "PL",
    "circle": "CI",
    "court": "CO",
    "ct": "CO",
    "close": "CL",
    "lane": "LN",
    "ln": "LN",
    "grove": "GV",
    "point": "PT",
    "green": "GR",
    "hill": "HL",
    "hills": "HL",
    "park": "PA",
    "rise": "RI",
    "gate": "GA",
    "link": "LI",
    "heath": "HE",
}
# Macleod Trail is the north-south divider, so its centreline octant is S
# while an address on it is written SW or SE. The same split is N for the
# Centre Street divider.
EDGE_OCTANT = {"SW": "S", "SE": "S", "NW": "N", "NE": "N"}


def street_key(name, street_type, quadrant):
    code = TYPE_CODES.get(street_type.casefold().rstrip("."))
    if code is None or not name or not quadrant:
        return None
    token = name.strip()
    number = ORDINALS.get(token.casefold())
    if number:
        token = number
    elif token.isdigit():
        token = str(int(token))
    else:
        token = token.upper()
    return f"{token}|{code}|{quadrant.upper()}"


class StreetIndex:
    def __init__(self, lines, houses):
        self.lines = lines
        self.houses = houses

    def candidates(self, address):
        local = address.local
        if not local:
            return []
        kind = local["kind"]
        if kind == "intersection":
            return self._intersections(local["left"], local["right"], address.quadrant)
        if kind == "civic":
            point = self._civic(
                local["name"], local["type"], address.quadrant, local["house"]
            )
        else:
            point = self._block(
                local["name"], local["type"], address.quadrant, local["house"]
            )
        if point is None:
            return []
        return [point]

    def _line_segments(self, name, street_type, quadrant):
        segments = []
        keys = [street_key(name, street_type, quadrant)]
        edge = EDGE_OCTANT.get(quadrant)
        if edge:
            keys.append(street_key(name, street_type, edge))
        for key in keys:
            if key is None:
                continue
            segments.extend(self.lines.get(key, []))
        return segments

    def _house_rows(self, name, street_type, quadrant):
        key = street_key(name, street_type, quadrant)
        rows = [] if key is None else self.houses.get(key, [])
        if rows:
            return rows
        edge = EDGE_OCTANT.get(quadrant)
        if edge is None:
            return []
        alias = street_key(name, street_type, edge)
        if alias is None:
            return []
        return self.houses.get(alias, [])

    def _intersections(self, left, right, quadrant):
        first = self._line_segments(left[0], left[1], quadrant)
        second = self._line_segments(right[0], right[1], quadrant)
        if not first or not second:
            return []
        found = []
        for segment in first:
            line = LineString(segment)
            if line.is_empty:
                continue
            for other in second:
                hit = line.intersection(LineString(other))
                if hit.is_empty:
                    continue
                if hit.geom_type == "Point":
                    found.append(hit)
                elif hit.geom_type == "MultiPoint":
                    found.extend(hit.geoms)
        unique = []
        for point in found:
            if any(point.distance(kept) < 1e-5 for kept in unique):
                continue
            unique.append(point)
        return unique

    def _civic(self, name, street_type, quadrant, house):
        rows = self._house_rows(name, street_type, quadrant)
        exact = [row for row in rows if row[0] == house]
        if exact:
            return _centroid(exact)
        # The parcel set has gaps. 3949 17 Avenue SW is not published.
        # Odd and even numbers sit on opposite sides of a Calgary street, so
        # the nearest published number on the same side is that house.
        start = (house // 100) * 100
        hundred = [row for row in rows if start <= row[0] <= start + 99]
        same_side = [row for row in hundred if row[0] % 2 == house % 2]
        if not same_side:
            return None
        nearest = min(same_side, key=lambda row: abs(row[0] - house))[0]
        return _centroid([row for row in same_side if row[0] == nearest])

    def _block(self, name, street_type, quadrant, house):
        rows = self._house_rows(name, street_type, quadrant)
        start = (house // 100) * 100
        hundred = [row for row in rows if start <= row[0] <= start + 99]
        if not hundred:
            return None
        return _centroid(hundred)


def _centroid(rows):
    lat = sum(row[1] for row in rows) / len(rows)
    lng = sum(row[2] for row in rows) / len(rows)
    return Point(lng, lat)


def load_street_index(path=STREET_INDEX_PATH):
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return StreetIndex(payload["lines"], payload["houses"])


def _fetch_page(resource, select, offset):
    params = urllib.parse.urlencode(
        {
            "$select": select,
            "$order": ":id",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }
    )
    url = f"https://data.calgary.ca/resource/{resource}.json?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context(cafile=certifi.where())
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, context=context, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503} or attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
        except urllib.error.URLError:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    return []


def _pages(resource, select):
    offset = 0
    while True:
        rows = _fetch_page(resource, select, offset)
        if not rows:
            break
        yield rows
        print(f"{resource} {offset + len(rows)}", flush=True)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE


def _coord(value):
    return round(float(value), 6)


def _segments(geometry):
    if not isinstance(geometry, dict):
        return []
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    parts = coordinates if kind == "MultiLineString" else [coordinates]
    segments = []
    for part in parts:
        if len(part) < 2:
            continue
        segments.append([[_coord(lng), _coord(lat)] for lng, lat in part])
    return segments


def build_street_index(path=STREET_INDEX_PATH):
    lines = {}
    for page in _pages(CENTRELINE_RESOURCE, "name,street_type,octant,line"):
        for row in page:
            name = row.get("name")
            street_type = row.get("street_type")
            octant = row.get("octant")
            if not name or not street_type or not octant:
                continue
            key = f"{str(name).upper()}|{str(street_type).upper()}|{str(octant).upper()}"
            bucket = lines.setdefault(key, [])
            bucket.extend(_segments(row.get("line")))

    houses = {}
    for page in _pages(
        ADDRESS_RESOURCE,
        "street_name,street_type,street_quad,house_number,latitude,longitude",
    ):
        for row in page:
            name = row.get("street_name")
            street_type = row.get("street_type")
            quadrant = row.get("street_quad")
            number = row.get("house_number")
            if not name or not street_type or not quadrant or number is None:
                continue
            try:
                house = int(number)
                lat = float(row["latitude"])
                lng = float(row["longitude"])
            except (TypeError, ValueError):
                continue
            key = f"{str(name).upper()}|{str(street_type).upper()}|{str(quadrant).upper()}"
            houses.setdefault(key, []).append([house, round(lat, 6), round(lng, 6)])

    path.write_text(
        json.dumps({"lines": lines, "houses": houses}, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"Wrote {path} ({path.stat().st_size} bytes, "
        f"{len(lines)} streets, {len(houses)} address keys)"
    )
    return path


if __name__ == "__main__":
    build_street_index()

"""Convert Calgary community boundaries to GeoJSON and dissolve address quadrants.

Address quadrants follow the City of Calgary lines, not planning sectors:

- North of the north/south line and west of Centre Street / Harvest Hills Boulevard: NW
- North of that line and east of it: NE
- South of that line and west of Centre Street S / Macleod Trail: SW
- South of that line and east of it: SE

The north/south line is 16 Avenue where the Bow bends north around Bowness, the Bow
through downtown, then Memorial Drive once the river turns south. Divider lines come
from OpenStreetMap, are saved to data/quadrant-dividers.geojson, and are reused later.
"""

import csv
import json
import ssl
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import certifi
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import substring, unary_union
from shapely.ops import split as split_polygon
from shapely.validation import make_valid
from shapely.wkt import loads as load_wkt

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "Community Boundaries Oct 28 2026.csv"
DATA_DIR = ROOT / "data"
DIVIDERS_PATH = DATA_DIR / "quadrant-dividers.geojson"
COMMUNITIES_PATH = DATA_DIR / "communities.geojson"
QUADRANTS_PATH = DATA_DIR / "quadrants.geojson"

OVERPASS_URLS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
OVERPASS_QUERY = """
[out:json][timeout:120];
(
  way["waterway"="river"]["name"="Bow River"](50.80,-114.70,51.25,-113.70);
  way["highway"]["name"="Centre Street N"](50.80,-114.40,51.25,-113.90);
  way["highway"]["name"="Centre Street S"](50.95,-114.10,51.06,-114.04);
  way["highway"]["name"~"^Macleod Trail"](50.80,-114.20,51.06,-113.90);
  way["name"~"^16 Avenue (NW|NE)"]["highway"~"^(trunk|motorway|primary)$"](51.00,-114.35,51.12,-113.85);
  way["name"~"^Memorial Drive (NW|NE|E|SE)$"]["highway"~"^(primary|secondary|trunk|motorway)$"](51.02,-114.25,51.08,-113.75);
  way["name"="Harvest Hills Boulevard N"](51.08,-114.15,51.22,-114.00);
);
out geom;
"""

QUADRANT_ORDER = ("NW", "NE", "SW", "SE")
QUADRANT_NAMES = {
    "NW": "Northwest",
    "NE": "Northeast",
    "SW": "Southwest",
    "SE": "Southeast",
}
# Points that sit clearly inside each side of a divider, used to label split pieces.
NORTH_PROBE = Point(-114.07, 51.10)
WEST_CENTRE_PROBE = Point(-114.15, 51.12)
EAST_MACLEOD_PROBE = Point(-113.95, 50.95)
# A community must overlap a quadrant by this share of its area to count as touching it.
TOUCH_FRACTION = 0.01

PROPERTY_FIELDS = (
    "CLASS",
    "CLASS_CODE",
    "COMM_CODE",
    "NAME",
    "SECTOR",
    "SRG",
    "COMM_STRUCTURE",
    "CREATED_DT",
    "MODIFIED_DT",
)

EXPECTED_QUADRANTS = {
    "EDGEMONT": "NW",
    "BOWNESS": "NW",
    "BRIDGELAND/RIVERSIDE": "NE",
    "CITYSCAPE": "NE",
    "LAKEVIEW": "SW",
    "SIGNAL HILL": "SW",
    "INGLEWOOD": "SE",
    "FOREST LAWN": "SE",
    "PANORAMA HILLS": "NW",
    "COVENTRY HILLS": "NE",
    "HARVEST HILLS": "NE",
}


def distance(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def remove_loops(coords, ndigits=5):
    """Drop out-and-back spikes where a chained road revisits a point."""
    cleaned = []
    index = {}
    for point in coords:
        key = (round(point[0], ndigits), round(point[1], ndigits))
        if key in index:
            cut = index[key]
            for dropped in cleaned[cut + 1 :]:
                index.pop((round(dropped[0], ndigits), round(dropped[1], ndigits)), None)
            del cleaned[cut + 1 :]
        else:
            index[key] = len(cleaned)
            cleaned.append(point)
    return cleaned


def chain_lines(segments, origin, tol=0.00045, heading_min=-0.2, max_tol=0.004):
    """Walk connected road or river segments into one linestring, starting near origin."""
    unused = list(segments)
    best_index, best_distance, reverse = None, 1e9, False
    for index, line in enumerate(unused):
        for flipped, point in ((False, line.coords[0]), (True, line.coords[-1])):
            gap = distance(point, origin)
            if gap < best_distance:
                best_distance, best_index, reverse = gap, index, flipped
    if best_index is None:
        raise RuntimeError("No divider segments to chain")
    line = unused.pop(best_index)
    coords = list(line.coords)
    if reverse:
        coords.reverse()
    current_tol = tol
    while unused:
        end = coords[-1]
        previous = coords[-2]
        heading = (end[0] - previous[0], end[1] - previous[1])
        heading_length = (heading[0] ** 2 + heading[1] ** 2) ** 0.5 or 1.0
        heading = (heading[0] / heading_length, heading[1] / heading_length)
        best = None
        for index, candidate in enumerate(unused):
            start, finish = candidate.coords[0], candidate.coords[-1]
            for flipped, point, other in ((False, start, finish), (True, finish, start)):
                gap = distance(end, point)
                if gap > current_tol:
                    continue
                direction = (other[0] - point[0], other[1] - point[1])
                direction_length = (direction[0] ** 2 + direction[1] ** 2) ** 0.5 or 1.0
                direction = (direction[0] / direction_length, direction[1] / direction_length)
                score = heading[0] * direction[0] + heading[1] * direction[1]
                if score < heading_min and gap > 0.00005:
                    continue
                key = (score, -gap)
                if best is None or key > best[0]:
                    best = (key, index, flipped)
        if best is None:
            if current_tol >= max_tol:
                break
            current_tol = min(max_tol, current_tol * 2)
            continue
        current_tol = tol
        _, index, flipped = best
        candidate = unused.pop(index)
        extra = list(candidate.coords)
        if flipped:
            extra.reverse()
        coords.extend(extra[1:] if distance(coords[-1], extra[0]) < 1e-8 else extra)
    coords = remove_loops(coords)
    if len(coords) < 2:
        raise RuntimeError("Divider chain collapsed")
    return LineString(coords)


def way_lines(elements, predicate):
    lines = []
    for element in elements:
        if not predicate(element.get("tags", {})):
            continue
        coords = []
        for point in element.get("geometry") or []:
            pair = (point["lon"], point["lat"])
            if not coords or pair != coords[-1]:
                coords.append(pair)
        if len(coords) >= 2:
            lines.append(LineString(coords))
    return lines


def fetch_overpass():
    context = ssl.create_default_context(cafile=certifi.where())
    payload = OVERPASS_QUERY.encode()
    errors = []
    for url in OVERPASS_URLS:
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"User-Agent": "yyc-signal-boundaries/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180, context=context) as response:
                data = json.load(response)
        except Exception as error:  # noqa: BLE001 - try the next Overpass mirror
            errors.append(f"{url}: {error}")
            continue
        elements = data.get("elements") or []
        if elements:
            return elements
        errors.append(f"{url}: no elements")
    raise RuntimeError("Overpass query failed:\n" + "\n".join(errors))


def build_dividers(elements):
    bow = chain_lines(
        way_lines(elements, lambda tags: tags.get("name") == "Bow River"),
        (-114.62, 51.21),
        tol=0.0002,
        heading_min=-1,
    )
    centre = chain_lines(
        way_lines(elements, lambda tags: tags.get("name") == "Centre Street N"),
        (-114.0626, 51.053),
    )
    centre_south = chain_lines(
        way_lines(elements, lambda tags: tags.get("name") == "Centre Street S"),
        (-114.0626, 51.0531),
    )
    macleod = chain_lines(
        way_lines(
            elements,
            lambda tags: str(tags.get("name", "")).startswith("Macleod Trail")
            and tags.get("highway") in {"primary", "trunk", "motorway"},
        ),
        (-114.058, 51.051),
        tol=0.0005,
        heading_min=-0.1,
    )
    sixteenth = chain_lines(
        way_lines(
            elements,
            lambda tags: str(tags.get("name", "")).startswith("16 Avenue")
            and tags.get("highway") in {"trunk", "motorway", "primary"},
        ),
        (-114.32, 51.08),
        heading_min=-0.5,
    )
    memorial = chain_lines(
        way_lines(
            elements,
            lambda tags: str(tags.get("name", "")).startswith("Memorial Drive")
            and tags.get("highway") in {"primary", "secondary", "trunk", "motorway"},
        ),
        (-114.20, 51.05),
        heading_min=-0.8,
        max_tol=0.008,
    )
    harvest = chain_lines(
        way_lines(elements, lambda tags: tags.get("name") == "Harvest Hills Boulevard N"),
        (-114.071, 51.13),
        heading_min=-0.2,
    )
    for name, line in (
        ("Bow River", bow),
        ("Centre Street", centre),
        ("Macleod Trail", macleod),
        ("Centre Street S", centre_south),
        ("16 Avenue", sixteenth),
        ("Memorial Drive", memorial),
        ("Harvest Hills Boulevard", harvest),
    ):
        if not line.is_simple:
            raise RuntimeError(f"{name} divider is self-intersecting")
    return (
        join_north_south(bow, sixteenth, memorial),
        join_north_east_west(centre, harvest),
        join_south_divider(centre_south, macleod),
    )


def orient_west_to_east(line):
    coords = list(line.coords)
    if coords[0][0] > coords[-1][0]:
        coords.reverse()
    return LineString(coords)


def join_north_south(bow, sixteenth, memorial):
    """16 Avenue around Bowness, the Bow through downtown, then Memorial Drive."""
    bow_line = LineString(list(bow.coords))
    avenue = orient_west_to_east(sixteenth)
    crossing = bow_line.intersection(avenue)
    if crossing.geom_type != "Point":
        raise RuntimeError(f"16 Avenue meets the Bow as {crossing.geom_type}, expected a point")
    avenue_west = substring(avenue, 0, avenue.project(crossing))
    memorial_line = orient_west_to_east(memorial)
    switch_distance = None
    step = max(bow_line.length / 400, 1e-4)
    distance_along = bow_line.project(crossing)
    while distance_along < bow_line.length:
        point = bow_line.interpolate(distance_along)
        gap = point.distance(memorial_line)
        if gap < 0.002:
            switch_distance = distance_along
        elif switch_distance is not None and gap > 0.004:
            break
        distance_along += step
    if switch_distance is None:
        raise RuntimeError("Bow River never meets Memorial Drive")
    switch = bow_line.interpolate(switch_distance)
    bow_middle = substring(bow_line, bow_line.project(crossing), switch_distance)
    memorial_east = substring(memorial_line, memorial_line.project(switch), memorial_line.length)
    coords = list(avenue_west.coords) + list(bow_middle.coords)[1:] + list(memorial_east.coords)
    line = LineString(remove_loops(coords))
    if not line.is_simple:
        raise RuntimeError("North/south divider is self-intersecting")
    return line


def join_north_east_west(centre, harvest):
    """Centre Street north from the Bow, then Harvest Hills Boulevard."""
    centre_coords = list(centre.coords)
    if centre_coords[0][1] > centre_coords[-1][1]:
        centre_coords.reverse()
    harvest_coords = list(harvest.coords)
    if harvest_coords[0][1] > harvest_coords[-1][1]:
        harvest_coords.reverse()
    line = LineString(remove_loops(centre_coords + harvest_coords))
    if not line.is_simple:
        raise RuntimeError("North east/west divider is self-intersecting")
    return line


def join_south_divider(centre_south, macleod):
    """Follow Centre Street S from the Bow, then Macleod Trail south."""
    centre_coords = list(centre_south.coords)
    if centre_coords[0][1] < centre_coords[-1][1]:
        centre_coords.reverse()
    macleod_coords = list(macleod.coords)
    if macleod_coords[0][1] < macleod_coords[-1][1]:
        macleod_coords.reverse()
    macleod_line = LineString(macleod_coords)
    join_at = macleod_line.project(Point(centre_coords[-1]))
    southern = substring(macleod_line, join_at, macleod_line.length)
    coords = centre_coords + [point for point in southern.coords if point != centre_coords[-1]]
    line = LineString(remove_loops(coords))
    if not line.is_simple:
        raise RuntimeError("South divider is self-intersecting")
    return line


def load_or_build_dividers():
    if DIVIDERS_PATH.exists():
        collection = json.loads(DIVIDERS_PATH.read_text(encoding="utf-8"))
        by_role = {
            feature["properties"]["role"]: shape(feature["geometry"])
            for feature in collection["features"]
        }
        return by_role["north_south"], by_role["east_west_north"], by_role["east_west_south"]
    elements = fetch_overpass()
    bow, centre, south = build_dividers(elements)
    features = [
        divider_feature(
            bow,
            "North/south quadrant line",
            "north_south",
            note="16 Avenue around Bowness, the Bow River through downtown, then Memorial Drive",
        ),
        divider_feature(
            centre,
            "Centre Street",
            "east_west_north",
            note="Centre Street N, then Harvest Hills Boulevard N",
        ),
        divider_feature(
            south,
            "Macleod Trail",
            "east_west_south",
            note="Includes Centre Street S from the Bow River to Macleod Trail",
        ),
    ]
    write_collection(DIVIDERS_PATH, features)
    return bow, centre, south


def divider_feature(line, name, role, note=None):
    properties = {"name": name, "role": role, "source": "OpenStreetMap"}
    if note:
        properties["note"] = note
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(round_geometry(line)),
    }


def round_geometry(geometry, ndigits=7):
    raw = mapping(geometry)

    def walk(value):
        if isinstance(value, float):
            return round(value, ndigits)
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    raw["coordinates"] = walk(raw["coordinates"])
    return shape(raw)


def span_west_east(line, bounds):
    minx, _, maxx, _ = bounds
    coords = list(line.coords)
    if coords[0][0] > coords[-1][0]:
        coords.reverse()
    if coords[0][0] > minx:
        coords.insert(0, (minx - 0.02, coords[0][1]))
    if coords[-1][0] < maxx:
        coords.append((maxx + 0.02, coords[-1][1]))
    return LineString(coords)


def span_north_south(line, bounds):
    _, miny, _, maxy = bounds
    coords = list(line.coords)
    if coords[0][1] < coords[-1][1]:
        coords.reverse()
    if coords[0][1] < maxy:
        coords.insert(0, (coords[0][0], maxy + 0.02))
    if coords[-1][1] > miny:
        coords.append((coords[-1][0], miny - 0.02))
    return LineString(coords)


def split_frame(frame, line, probe):
    parts = [
        part
        for part in split_polygon(frame, line).geoms
        if part.geom_type in {"Polygon", "MultiPolygon"} and part.area > 1e-8
    ]
    if len(parts) != 2:
        raise RuntimeError(f"Divider split produced {len(parts)} pieces, expected 2")
    containing = [part for part in parts if part.contains(probe)]
    if len(containing) != 1:
        raise RuntimeError("Could not label a divider split from the probe point")
    chosen = make_valid(containing[0])
    other = make_valid(frame.difference(chosen))
    return chosen, other


def quadrant_regions(communities, bow, centre, macleod):
    bounds = unary_union([geometry for geometry, _ in communities]).bounds
    minx, miny, maxx, maxy = bounds
    frame = box(minx - 0.02, miny - 0.02, maxx + 0.02, maxy + 0.02)
    north, _south_frame = split_frame(frame, span_west_east(bow, frame.bounds), NORTH_PROBE)
    south = make_valid(frame.difference(north))
    west_of_centre, _east_of_centre = split_frame(
        frame, span_north_south(centre, frame.bounds), WEST_CENTRE_PROBE
    )
    east_of_macleod, _west_piece = split_frame(
        frame, span_north_south(macleod, frame.bounds), EAST_MACLEOD_PROBE
    )
    west_of_macleod = make_valid(frame.difference(east_of_macleod))
    regions = {
        "NW": make_valid(north.intersection(west_of_centre)),
        "NE": make_valid(north.difference(west_of_centre)),
        "SW": make_valid(south.intersection(west_of_macleod)),
        "SE": make_valid(south.difference(west_of_macleod)),
    }
    return regions


def polygonal(geometry):
    geometry = make_valid(geometry)
    if geometry.is_empty:
        return geometry
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    if geometry.geom_type == "GeometryCollection":
        parts = [
            part
            for part in geometry.geoms
            if part.geom_type in {"Polygon", "MultiPolygon"} and not part.is_empty
        ]
        return unary_union(parts) if parts else geometry
    return geometry


def load_communities():
    csv.field_size_limit(sys.maxsize)
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    communities = []
    for row in rows:
        geometry = make_valid(load_wkt(row["MULTIPOLYGON"]))
        if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise RuntimeError(f"{row['NAME']} has no polygon")
        communities.append((geometry, row))
    return communities


def assign_communities(communities, regions):
    """Return community records and the polygon pieces that dissolve into quadrants."""
    records = []
    pieces = defaultdict(list)
    for geometry, row in communities:
        overlaps = []
        for code in QUADRANT_ORDER:
            piece = polygonal(geometry.intersection(regions[code]))
            if piece.is_empty or piece.area <= 0:
                continue
            overlaps.append((code, piece, piece.area))
        if not overlaps:
            raise RuntimeError(f"{row['NAME']} did not fall into a quadrant")
        total = sum(area for _, _, area in overlaps)
        for code, piece, area in overlaps:
            if area / total >= 1e-8:
                pieces[code].append(piece)
        counted = [item for item in overlaps if item[2] / total >= TOUCH_FRACTION]
        if not counted:
            counted = [max(overlaps, key=lambda item: item[2])]
        counted.sort(key=lambda item: item[2], reverse=True)
        codes = [code for code in QUADRANT_ORDER if code in {item[0] for item in counted}]
        properties = {field: row[field] for field in PROPERTY_FIELDS}
        properties["quadrant"] = counted[0][0]
        properties["quadrants"] = "/".join(codes)
        records.append((geometry, properties))
    return records, pieces


def feature(geometry, properties, feature_id=None):
    payload = {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(round_geometry(make_valid(geometry))),
    }
    if feature_id is not None:
        payload["id"] = feature_id
    return payload


def write_collection(path, features):
    path.parent.mkdir(parents=True, exist_ok=True)
    collection = {"type": "FeatureCollection", "features": features}
    with path.open("w", encoding="utf-8") as handle:
        json.dump(collection, handle, separators=(",", ":"))
        handle.write("\n")


def validate(records, quadrant_features):
    errors = []
    if len(records) != 313:
        errors.append(f"expected 313 communities, found {len(records)}")
    if len(quadrant_features) != 4:
        errors.append(f"expected 4 quadrants, found {len(quadrant_features)}")
    by_name = {properties["NAME"]: properties for _geometry, properties in records}
    for name, quadrant in EXPECTED_QUADRANTS.items():
        found = by_name.get(name)
        if found is None:
            errors.append(f"missing community {name}")
        elif found["quadrant"] != quadrant:
            errors.append(f"{name} is {found['quadrant']}, expected {quadrant}")
    signal_hill = by_name.get("SIGNAL HILL")
    if signal_hill and signal_hill["SECTOR"] != "WEST":
        errors.append(f"Signal Hill sector is {signal_hill['SECTOR']}, expected WEST")
    bowness = by_name.get("BOWNESS")
    fish = by_name.get("FISH CREEK PARK")
    if fish is None or fish["quadrants"] != "SW/SE":
        errors.append(f"Fish Creek quadrants are {None if fish is None else fish['quadrants']}")
    empty = [
        feature["properties"]["quadrant"]
        for feature in quadrant_features
        if shape(feature["geometry"]).is_empty
    ]
    if empty:
        errors.append(f"empty quadrant geometries: {empty}")
    community_union = unary_union([geometry for geometry, _properties in records])
    quadrant_union = unary_union([shape(feature["geometry"]) for feature in quadrant_features])
    gap = community_union.difference(quadrant_union).area
    extra = quadrant_union.difference(community_union).area
    relative_gap = gap / community_union.area
    relative_extra = extra / community_union.area
    if relative_gap > 1e-4 or relative_extra > 1e-4:
        errors.append(
            f"quadrant coverage differs from communities "
            f"(missing {relative_gap:.6f}, extra {relative_extra:.6f})"
        )
    if errors:
        raise SystemExit("Validation failed:\n- " + "\n- ".join(errors))
    straddling = [
        properties["NAME"] + " " + properties["quadrants"]
        for _geometry, properties in records
        if "/" in properties["quadrants"]
    ]
    print(f"Communities: {len(records)}")
    print(f"Quadrants: {', '.join(feature['properties']['quadrant'] for feature in quadrant_features)}")
    print(f"Coverage gap {relative_gap:.8f}, extra {relative_extra:.8f}")
    print("Straddling:", ", ".join(straddling) if straddling else "none")
    print(
        "Signal Hill sector",
        signal_hill["SECTOR"],
        "quadrant",
        signal_hill["quadrant"],
    )
    print("Bowness sector", bowness["SECTOR"], "quadrant", bowness["quadrant"])


def main():
    bow, centre, south = load_or_build_dividers()
    communities = load_communities()
    regions = quadrant_regions(communities, bow, centre, south)
    records, pieces = assign_communities(communities, regions)
    community_features = [
        feature(geometry, properties, properties["COMM_CODE"]) for geometry, properties in records
    ]
    quadrant_features = []
    for code in QUADRANT_ORDER:
        geometry = unary_union(pieces[code])
        quadrant_features.append(
            feature(
                geometry,
                {"quadrant": code, "name": QUADRANT_NAMES[code]},
                code,
            )
        )
    write_collection(COMMUNITIES_PATH, community_features)
    write_collection(QUADRANTS_PATH, quadrant_features)
    validate(records, quadrant_features)
    print(f"Wrote {COMMUNITIES_PATH.relative_to(ROOT)}")
    print(f"Wrote {QUADRANTS_PATH.relative_to(ROOT)}")
    print(f"Wrote {DIVIDERS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

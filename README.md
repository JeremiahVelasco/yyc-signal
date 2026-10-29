# YYC Signal

A map-first view of Calgary newsroom releases, tagged by neighbourhood, quadrant, and landmark. Data is refreshed daily from public RSS feeds; the site is a static React app with no backend.

**Code:** [github.com/JeremiahVelasco/yyc-signal](https://github.com/JeremiahVelasco/yyc-signal)

For the problem statement, accuracy study, and design decisions, see [docs/project-writeup.md](docs/project-writeup.md).

## How it works

```text
Newsroom RSS → ingest/main.py → data/articles.json → web (Vite) → Vercel
                     ↑
     communities, landmarks, street index, Nominatim cache
```

- **Ingest** ([`ingest/main.py`](ingest/main.py)): fetches four [newsroom.calgary.ca](https://newsroom.calgary.ca) tag feeds (police, fire, city, feature), tags each item with a location, merges into the existing JSON, and drops items older than 30 days.
- **Reference data** ([`data/`](data/)): community polygons and quadrants, [`landmarks.json`](data/landmarks.json), and a local [`street-index.json`](data/street-index.json) built from City of Calgary open data.
- **Automation** ([`.github/workflows/ingest.yml`](.github/workflows/ingest.yml)): daily at 14:00 UTC (plus manual runs), runs unit tests, ingest, and commits `articles.json` / `geocode-cache.json` only when they change. Vercel redeploys on that commit.
- **Frontend** ([`web/`](web/)): React + Vite; imports [`data/articles.json`](data/articles.json) at build time. Map via Mapbox (`react-map-gl`). Routes: `/` (map + list), `/about`.

Tagging order: street address → landmark → community name → quadrant only. Articles without coordinates appear in **Not on map** (including quadrant-only items). Landmark ids mentioned in the text are stored even when the pin comes from an address or community.

## Repository layout

| Path | Purpose |
| --- | --- |
| `ingest/` | Python CLI, tests, optional scripts (`streets.py`, `boundaries_to_geojson.py`) |
| `data/` | Committed article snapshot, geocode cache, GeoJSON, landmarks, street index |
| `web/` | Frontend app and `public/landmarks.json` (synced from ingest) |
| `vercel.json` | Builds `web/` from repo root |

## Local development

### Ingest

```bash
cd ingest
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m unittest          # regression tests (uses cached geocode, no network for pins)
python main.py              # fetch feeds; needs network; updates ../data/
```

Optional: `python main.py retag` re-tags `data/articles.json` in place using the geocode cache only; exits if any pin field changes.

Rebuild street index from Calgary open data (infrequent):

```bash
python streets.py
```

Community/quadrant GeoJSON from a boundaries CSV (infrequent):

```bash
python boundaries_to_geojson.py
```

### Web

```bash
cd web
npm ci
```

Create `web/.env.local` (not committed):

```env
ACCESS_TOKEN=your_mapbox_token
STYLE_URL=mapbox://styles/your-account/your-style-id
```

Vite also accepts `MAPBOX_*` prefixes; the app reads `ACCESS_TOKEN` and `STYLE_URL` ([`web/src/App.jsx`](web/src/App.jsx)).

```bash
npm run dev
```

Open the dev server URL. The app reads articles from `../data/articles.json` via Vite’s repo-root allowlist.

## Data files

| File | Role |
| --- | --- |
| `data/articles.json` | Tagged articles served to the UI |
| `data/geocode-cache.json` | Nominatim results keyed by query |
| `data/communities.geojson` | Community boundaries + quadrant labels |
| `data/quadrants.geojson` | NW/NE/SW/SE polygons for address validation |
| `data/landmarks.json` | Curated landmark phrases and coordinates |
| `data/street-index.json` | Centreline + parcel-derived address lookup |

## Deployment

- **Frontend:** Vercel, config in [`vercel.json`](vercel.json) (`web/dist`).
- **Data updates:** GitHub Actions only; no ingest service in production.

## Licence and attribution

This project is independent and not operated by the City of Calgary.

### Code

The application source code in this repository is licensed under the [MIT License](LICENSE). That grant does not apply to third-party data bundled in `data/` or to news text.

### Article text

Titles and summaries in [`data/articles.json`](data/articles.json) come from the City of Calgary newsroom RSS feeds and belong to their original publishers. This project does not license that text. Each item links to the canonical release on [newsroom.calgary.ca](https://newsroom.calgary.ca).

### City of Calgary open data

The following datasets are used under the [Open Government Licence – City of Calgary](https://data.calgary.ca/d/Open-Data-Terms/u45n-7awa) (version 2.1). Required attribution:

> Contains information licensed under the Open Government Licence – City of Calgary.

| Dataset | ID | Used in |
| --- | --- | --- |
| [Community Boundaries](https://data.calgary.ca/Base-Maps/Community-Boundaries/ab7m-fwn6) | `ab7m-fwn6` | `data/communities.geojson`; community polygons in `data/quadrants.geojson` |
| [Street Centreline](https://data.calgary.ca/Transportation-Transit/Street-Centreline/4dx8-rtm5) | `4dx8-rtm5` | Centreline portion of `data/street-index.json` |
| [Parcel Address and lat/long](https://data.calgary.ca/Base-Maps/Parcel-Address-and-lat-long/s8b3-j88p) | `s8b3-j88p` | Parcel portion of `data/street-index.json` |

### OpenStreetMap

Geocoding during ingest uses [Nominatim](https://nominatim.openstreetmap.org/), which returns data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors under the [Open Database Licence (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/). The committed [`data/geocode-cache.json`](data/geocode-cache.json) redistributes those results and must carry that credit.

[`data/quadrant-dividers.geojson`](data/quadrant-dividers.geojson) is derived from OpenStreetMap via Overpass when building quadrants and is likewise ODbL.

### Other data

[`data/landmarks.json`](data/landmarks.json) is compiled for this project (not a City or OSM extract) and is covered by the MIT licence.

### Map basemap

The interactive map uses Mapbox GL JS and Mapbox-hosted tiles. Mapbox’s attribution control remains visible on the map; Mapbox terms apply to the basemap, not to files in this repo.

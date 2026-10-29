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

## License / attribution

News text and links point to the City of Calgary newsroom. This project is independent and not operated by the City. Map: Mapbox. Geocoding during ingest may use OpenStreetMap via Nominatim. Community and street data: City of Calgary open data.

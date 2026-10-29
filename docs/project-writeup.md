# YYC Signal: Project Write-up

A map of Calgary news, tagged by neighbourhood, quadrant, and landmark, refreshed daily from the City newsroom RSS feeds.

**Code:** [github.com/JeremiahVelasco/yyc-signal](https://github.com/JeremiahVelasco/yyc-signal)

## Overview

YYC Signal pulls local news from RSS feeds on a daily schedule, works out where each story happened, and shows it on a map. Visitors can filter by quadrant (NW/NE/SE/SW), community, source, and landmark mentioned. Stories that can't be pinned to one place—citywide releases, multi-community lists, or text with no reliable location—appear in a separate **Not on map** list instead of disappearing. Quadrant-only tags (no coordinates) also show there; filters can still narrow by quadrant.

## The problem

News feeds give you headlines and short summaries, not coordinates. A headline like "Fire closes road in Bridgeland" is easy for a person to place, but a machine has to deal with street addresses, landmarks, community names that are also common words or surnames, and stories with no location at all. Getting this tagging right was the real project. The map came after.

## Architecture

- **Ingest (Python CLI):** fetches RSS feeds with feedparser, tags each article, merges new items into the existing data, and writes `articles.json`. Copies `landmarks.json` into `web/public/` when articles are saved.
- **Reference data:** Calgary community boundaries (with quadrant) in GeoJSON, a street centreline and parcel-point index in `street-index.json` built from City of Calgary open data, and a hand-curated `landmarks.json`.
- **Automation (GitHub Actions):** a daily workflow (14:00 UTC, plus manual dispatch) runs regression tests, runs ingest, and commits only when `articles.json` or `geocode-cache.json` changed.
- **Frontend (React + Vite):** reads the static JSON at build time and renders the map, filters, stats, and **Not on map** panel. `/about` explains sources and limitations.
- **Hosting (Vercel):** serves only the static `web/` app. A bot commit from the workflow triggers a redeploy, so there is no backend server to run.

This keeps hosting close to free and removes servers, databases, and schedulers from the list of things that can break.

## How tagging works

Rules run in a fixed order of precision:

1. **Street address first.** Patterns like "1700 block of 35 Avenue S.W." and civic addresses are resolved against the local street index, with Nominatim as a fallback. An address always beats any other name in the article. If an address is found but cannot be placed, the article keeps that address's quadrant and does **not** fall through to a community name from elsewhere in the text.
2. **Landmarks next.** Matched text is consumed so it can't be matched again as something else. "Foothills Medical Centre" is a landmark and never the community Foothills. Landmark ids mentioned in the title or summary are stored on every article, in order of first appearance, even when the pin came from an address or a community.
3. **Community names last.** Four or more distinct community names in one story are treated as a list, not one place. One-word names need context ("in", "near", "community of") and are blocked after a rank like "Insp." or before a facility word like "Inn" or "Centre".
4. **Quadrant only** if nothing more precise is found.

Every geocoder hit is cross-checked: the result must land inside the address-quadrant polygon, and the community label comes from the polygon that contains the point. On long streets (avenues, streets, trails), Nominatim's display name must still contain the house or block number from the query. The local index can snap a missing parcel to the nearest same-side house number in that hundred-block when city data has gaps.

Geocoder results are cached by query, which keeps daily runs polite to Nominatim.

## Measuring accuracy

I hand-labelled a 50-article sample and re-measured after each round of fixes.

| Stage | Pinned on map |
| --- | --- |
| First baseline | 21 of 50 |
| After address geocoding and name fixes | 32 of 50 |
| After local street index | 38 of 50 |

Of the 50 articles, 10 have no single location (citywide releases, a list of about 20 communities, a police inspector whose surname matches a community). That leaves 40 locatable articles, and 38 of them are pinned—about 95%. Two are quadrant-only because the data couldn't place them confidently.

Caveat: the rules were tuned on this same sample, so 95% is a ceiling. The sample is frozen as a regression test (`test_frozen_fifty` re-tags the full committed `articles.json` offline and asserts pin fields and landmark lists do not change).

The live corpus is larger and unlabelled. As of the committed snapshot, **195 articles** break down as **109 pinned**, **28 quadrant-only**, and **58 with no quadrant or pin**—useful for volume and failure modes, not as a new accuracy score until fresh articles are hand-checked.

## Bugs that shaped the design

- **Name collisions.** Foothills Medical Centre was matching the community Foothills, and the Red Carpet Inn was matching the community Red Carpet. A police inspector named Patterson matched the Patterson community. The fix was general: landmarks consume their text first, and risky one-word names need context.
- **Wrong place in the story.** In one armed-robbery story the suspect was arrested in a different community from where the crime happened. The rule now is that an address beats any other name.
- **Plausible but wrong geocodes.** Two addresses landed in the wrong community. The quadrant polygon and house-number checks caught them.
- **Nonexistent addresses.** One address in a story doesn't exist in the city's parcel data. The pin uses the nearest same-side address in that block; a future `location_precision` field would make that explicit.

## Decisions I'd highlight

- **Pipeline before map.** The risk was in the tagging, so I built and measured that first.
- **Rules before an LLM.** I skipped the AI step until the failures were understood. Most misses turned out to be rule and data problems that a model couldn't fix either.
- **A wrong pin is worse than no pin.** Uncertain articles go to **Not on map** (no lat/lng), or keep quadrant-only metadata without a pin.
- **Static deployment.** The CLI was never an HTTP service, so I deployed only the frontend and let GitHub Actions do the scheduling.
- **Frozen regression tests.** Hand-verified pins are protected, so a rule change can't silently reintroduce an old bug.
- **Merge, don't overwrite.** Saved rows win on duplicate links (feed priority: police, fire, city, feature). If every feed fails, `articles.json` is left unchanged.

## Tech stack

Python 3.13, feedparser, Shapely, Nominatim, City of Calgary open data, React 19, Vite, react-map-gl, Mapbox GL JS, GitHub Actions, Vercel.

## What's next

- Test on a fresh batch of articles to get an honest accuracy number.
- Add `location_precision` (exact, block, intersection, street, community, quadrant) and `scope` (citywide, multi-community) fields; wire `location_method` if useful for debugging.
- Add more sources (CBC, CTV) and dedupe stories across outlets (link dedupe within newsroom feeds already exists).
- Log unpinned articles with reasons and expand the landmark list from real headlines.
- Move to an API and database only if search or an archive becomes necessary.

## Lessons learned

1. Measure first. Hand-labelling 50 articles told me more than any amount of guessing.
2. Fix classes of bugs, not single cases.
3. Make the pipeline safe to run unattended: merge instead of overwrite, skip empty commits, and never let a failed feed wipe data.
4. Keep the architecture as small as the problem allows.

import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import MapView, { Marker, Popup } from 'react-map-gl/mapbox'
import articles from '../../data/articles.json'
import MultiCombobox from './MultiCombobox'
import 'mapbox-gl/dist/mapbox-gl.css'
import './App.css'

const QUADRANTS = ['NW', 'NE', 'SW', 'SE']
const SOURCES = [
  { id: 'police', label: 'Police' },
  { id: 'fire', label: 'Fire' },
  { id: 'city', label: 'City' },
  { id: 'feature', label: 'Feature' },
]
const TOKEN = import.meta.env.ACCESS_TOKEN
const STYLE_URL = import.meta.env.STYLE_URL

function placeName(value) {
  if (!value) return ''
  return value
    .toLowerCase()
    .replace(/\b[a-z]/g, (letter) => letter.toUpperCase())
}

function formatPublished(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-CA', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function articleKey(article) {
  return article.link || article.title
}

function articleSource(article) {
  return article.source || 'police'
}

function sourceLabel(id) {
  return SOURCES.find((source) => source.id === id)?.label ?? id
}

const SOURCE_EMOJI = {
  police: '🚔',
  fire: '🔥',
  city: '🏛️',
  feature: '📰',
}

function sourceEmoji(source) {
  return SOURCE_EMOJI[source] ?? SOURCE_EMOJI.police
}

function parsePublished(value) {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function dayKey(date) {
  return date.toLocaleDateString('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

function eachDay(start, end) {
  const days = []
  const cursor = new Date(start)
  cursor.setHours(0, 0, 0, 0)
  const last = new Date(end)
  last.setHours(0, 0, 0, 0)
  while (cursor <= last) {
    days.push(new Date(cursor))
    cursor.setDate(cursor.getDate() + 1)
  }
  return days
}

function rankedCounts(entries) {
  return [...entries.entries()].sort(
    (left, right) => right[1] - left[1] || String(left[0]).localeCompare(String(right[0])),
  )
}

const NEIGHBORHOOD_LIMIT = 8

function buildStats(articles, landmarkById) {
  const neighborhoodCounts = new Map()
  const landmarkCounts = new Map()
  const quadrantCounts = Object.fromEntries(QUADRANTS.map((code) => [code, 0]))
  const dayCounts = new Map()
  let onMap = 0
  let withNeighborhood = 0
  let withQuadrant = 0
  let withLandmark = 0
  let unlocated = 0
  let untaggedQuadrant = 0
  let oldest = null
  let newest = null

  for (const article of articles) {
    const located = article.lat != null && article.lng != null
    if (located) onMap += 1
    if (article.neighborhood) {
      withNeighborhood += 1
      neighborhoodCounts.set(
        article.neighborhood,
        (neighborhoodCounts.get(article.neighborhood) ?? 0) + 1,
      )
    }
    if (article.quadrant && quadrantCounts[article.quadrant] != null) {
      withQuadrant += 1
      quadrantCounts[article.quadrant] += 1
    } else {
      untaggedQuadrant += 1
    }
    if (!located && !article.quadrant) unlocated += 1
    const landmarks = article.landmarks ?? []
    if (landmarks.length > 0) withLandmark += 1
    for (const id of landmarks) {
      landmarkCounts.set(id, (landmarkCounts.get(id) ?? 0) + 1)
    }
    const published = parsePublished(article.published)
    if (!published) continue
    if (!oldest || published < oldest) oldest = published
    if (!newest || published > newest) newest = published
    const key = dayKey(published)
    dayCounts.set(key, (dayCounts.get(key) ?? 0) + 1)
  }

  const neighborhoods = rankedCounts(neighborhoodCounts).map(([name, count]) => ({
    label: placeName(name),
    count,
  }))
  const neighborhoodRows =
    neighborhoods.length <= NEIGHBORHOOD_LIMIT
      ? neighborhoods
      : neighborhoods.slice(0, NEIGHBORHOOD_LIMIT)
  const neighborhoodRemainder = neighborhoods
    .slice(neighborhoodRows.length)
    .reduce((total, row) => total + row.count, 0)

  const landmarks = rankedCounts(landmarkCounts).map(([id, count]) => ({
    label: landmarkById.get(id)?.name ?? id,
    count,
  }))

  const quadrants = QUADRANTS.map((code) => ({ label: code, count: quadrantCounts[code] }))
  if (untaggedQuadrant > 0) {
    quadrants.push({ label: 'Untagged', count: untaggedQuadrant })
  }

  const days =
    oldest && newest
      ? eachDay(oldest, newest).map((date) => ({
          key: dayKey(date),
          label: date.toLocaleDateString('en-CA', { month: 'short', day: 'numeric' }),
          count: dayCounts.get(dayKey(date)) ?? 0,
        }))
      : []

  return {
    total: articles.length,
    onMap,
    withNeighborhood,
    withQuadrant,
    withLandmark,
    unlocated,
    quadrants,
    neighborhoods: neighborhoodRows,
    neighborhoodRemainder,
    landmarks,
    oldest: oldest ? formatPublished(oldest) : '',
    newest: newest ? formatPublished(newest) : '',
    days,
  }
}

function emptyFilters() {
  return { neighborhoods: [], quadrants: [], sources: [], landmarks: [] }
}

function unique(values) {
  return [...new Set(values)]
}

function readFilters() {
  const params = new URLSearchParams(window.location.search)
  return {
    neighborhoods: unique(params.getAll('neighborhood')),
    quadrants: unique(params.getAll('quadrant').filter((code) => QUADRANTS.includes(code))),
    sources: unique(params.getAll('source').filter((id) => SOURCES.some((source) => source.id === id))),
    landmarks: unique(params.getAll('landmark')),
  }
}

function filtersReducer(state, action) {
  if (action.type === 'clear') return emptyFilters()
  const current = state[action.field]
  if (action.type === 'add') {
    if (current.includes(action.value)) return state
    return { ...state, [action.field]: [...current, action.value] }
  }
  if (action.type === 'remove') {
    return { ...state, [action.field]: current.filter((value) => value !== action.value) }
  }
  if (action.type === 'toggle') {
    const next = current.includes(action.value)
      ? current.filter((value) => value !== action.value)
      : [...current, action.value]
    return { ...state, [action.field]: next }
  }
  return state
}

function hasActiveFilters(filters) {
  return (
    filters.neighborhoods.length > 0 ||
    filters.quadrants.length > 0 ||
    filters.sources.length > 0 ||
    filters.landmarks.length > 0
  )
}

function articleMatches(article, filters) {
  if (filters.sources.length && !filters.sources.includes(articleSource(article))) return false
  if (filters.neighborhoods.length && !filters.neighborhoods.includes(article.neighborhood)) return false
  if (filters.quadrants.length && !filters.quadrants.includes(article.quadrant)) return false
  if (
    filters.landmarks.length &&
    !filters.landmarks.some((id) => (article.landmarks ?? []).includes(id))
  ) {
    return false
  }
  return true
}

function hasPin(article) {
  return article.lat != null && article.lng != null
}

function writeFilters(filters) {
  const params = new URLSearchParams()
  for (const value of filters.neighborhoods) params.append('neighborhood', value)
  for (const value of filters.quadrants) params.append('quadrant', value)
  for (const value of filters.sources) params.append('source', value)
  for (const value of filters.landmarks) params.append('landmark', value)
  const next = params.toString()
  const current = window.location.search.replace(/^\?/, '')
  if (next === current) return
  const url = next ? `${window.location.pathname}?${next}` : window.location.pathname
  window.history.replaceState(null, '', url)
}

function fitMap(map, pins) {
  if (!map || pins.length === 0) return
  let minLng = pins[0].lng
  let minLat = pins[0].lat
  let maxLng = pins[0].lng
  let maxLat = pins[0].lat
  for (const pin of pins) {
    minLng = Math.min(minLng, pin.lng)
    minLat = Math.min(minLat, pin.lat)
    maxLng = Math.max(maxLng, pin.lng)
    maxLat = Math.max(maxLat, pin.lat)
  }
  if (pins.length === 1 || (minLng === maxLng && minLat === maxLat)) {
    map.flyTo({ center: [minLng, minLat], zoom: 13, duration: 700 })
    return
  }
  map.fitBounds(
    [
      [minLng, minLat],
      [maxLng, maxLat],
    ],
    { padding: 48, maxZoom: 14, duration: 700 },
  )
}

function BarList({ rows }) {
  const max = Math.max(1, ...rows.map((row) => row.count))
  return (
    <ul className="bar-list">
      {rows.map((row) => (
        <li key={row.key ?? row.label}>
          <span className="bar-label">{row.label}</span>
          <span className="bar-track">
            <span
              className="bar-fill"
              style={{
                width: `${(row.count / max) * 100}%`,
                minWidth: row.count > 0 ? 2 : 0,
              }}
            />
          </span>
          <span className="bar-count">{row.count}</span>
        </li>
      ))}
    </ul>
  )
}

function StatsView({ stats }) {
  return (
    <div className="stats">
      <section>
        <h2>Coverage</h2>
        <dl className="coverage">
          <div>
            <dt>Articles</dt>
            <dd>{stats.total}</dd>
          </div>
          <div>
            <dt>On the map</dt>
            <dd>{stats.onMap}</dd>
          </div>
          <div>
            <dt>Neighborhood</dt>
            <dd>{stats.withNeighborhood}</dd>
          </div>
          <div>
            <dt>Quadrant</dt>
            <dd>{stats.withQuadrant}</dd>
          </div>
          <div>
            <dt>Landmark</dt>
            <dd>{stats.withLandmark}</dd>
          </div>
          <div>
            <dt>Unlocated</dt>
            <dd>{stats.unlocated}</dd>
          </div>
        </dl>
      </section>
      <section>
        <h2>Quadrants</h2>
        <BarList rows={stats.quadrants} />
      </section>
      {stats.neighborhoods.length > 0 && (
        <section>
          <h2>Neighborhoods</h2>
          <BarList rows={stats.neighborhoods} />
          {stats.neighborhoodRemainder > 0 && (
            <p className="remainder">{stats.neighborhoodRemainder} in other neighborhoods</p>
          )}
        </section>
      )}
      {stats.landmarks.length > 0 && (
        <section>
          <h2>Landmarks</h2>
          <BarList rows={stats.landmarks} />
        </section>
      )}
      {(stats.oldest || stats.days.length > 0) && (
        <section>
          <h2>Dates</h2>
          {stats.oldest && (
            <p className="date-span">
              {stats.oldest}
              {stats.newest && stats.newest !== stats.oldest ? ` – ${stats.newest}` : ''}
            </p>
          )}
          {stats.days.length > 0 && <BarList rows={stats.days} />}
        </section>
      )}
    </div>
  )
}

function ArticleItem({ article, selected, names, onSelect }) {
  const where = [placeName(article.neighborhood), article.quadrant].filter(Boolean).join(' · ')
  return (
    <li>
      <button
        type="button"
        className={selected ? 'selected' : undefined}
        onClick={() => onSelect(article, true)}
      >
        <span className="article-title">{article.title}</span>
        <span className="article-meta">
          {sourceLabel(articleSource(article))}
          {' · '}
          {formatPublished(article.published)}
          {where ? ` · ${where}` : ''}
          {names ? ` · ${names}` : ''}
        </span>
      </button>
    </li>
  )
}

export default function App() {
  const mapRef = useRef(null)
  const fittedSignature = useRef(null)
  const [catalog, setCatalog] = useState([])
  const [filters, dispatch] = useReducer(filtersReducer, null, readFilters)
  const [selectedKey, setSelectedKey] = useState(null)
  const [view, setView] = useState('list')
  const [mapReady, setMapReady] = useState(false)
  const [showLandmarks, setShowLandmarks] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch('/landmarks.json')
      .then((response) => response.json())
      .then((data) => {
        if (!cancelled) setCatalog(data.landmarks ?? [])
      })
      .catch(() => {
        if (!cancelled) setCatalog([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  const landmarkById = useMemo(() => {
    return new Map(catalog.map((landmark) => [landmark.id, landmark]))
  }, [catalog])

  const neighborhoods = useMemo(() => {
    return [...new Set(articles.map((article) => article.neighborhood).filter(Boolean))].sort(
      (left, right) => left.localeCompare(right),
    )
  }, [])

  const landmarkOptions = useMemo(() => {
    return [...catalog].sort((left, right) => left.name.localeCompare(right.name))
  }, [catalog])

  const neighborhoodOptions = useMemo(() => {
    return neighborhoods.map((name) => ({ value: name, label: placeName(name) }))
  }, [neighborhoods])

  const quadrantOptions = useMemo(() => {
    return QUADRANTS.map((code) => ({ value: code, label: code }))
  }, [])

  const sourceOptions = useMemo(() => {
    return SOURCES.map((item) => ({ value: item.id, label: item.label }))
  }, [])

  const landmarkChoices = useMemo(() => {
    return landmarkOptions.map((landmark) => ({ value: landmark.id, label: landmark.name }))
  }, [landmarkOptions])

  const filtered = useMemo(() => {
    return articles.filter((article) => articleMatches(article, filters))
  }, [filters])

  const stats = useMemo(
    () => buildStats(filtered, landmarkById),
    [filtered, landmarkById],
  )

  const selected = filtered.find((article) => articleKey(article) === selectedKey) ?? null
  const mapped = filtered.filter((article) => hasPin(article))
  const offMap = filtered.filter((article) => !hasPin(article))

  useEffect(() => {
    writeFilters(filters)
  }, [filters])

  useEffect(() => {
    if (!mapReady) return
    const signature = JSON.stringify(filters)
    const previous = fittedSignature.current
    if (previous === signature) return
    fittedSignature.current = signature
    if (previous === null && !hasActiveFilters(filters)) return
    const pins = articles.filter((article) => articleMatches(article, filters) && hasPin(article))
    fitMap(mapRef.current, pins)
  }, [filters, mapReady])

  const chips = [
    ...filters.neighborhoods.map((value) => ({
      field: 'neighborhoods',
      value,
      label: placeName(value),
    })),
    ...filters.quadrants.map((value) => ({ field: 'quadrants', value, label: value })),
    ...filters.sources.map((value) => ({
      field: 'sources',
      value,
      label: sourceLabel(value),
    })),
    ...filters.landmarks.map((value) => ({
      field: 'landmarks',
      value,
      label: landmarkById.get(value)?.name ?? value,
    })),
  ]

  function landmarkLabel(article) {
    return (article.landmarks ?? [])
      .map((id) => landmarkById.get(id)?.name)
      .filter(Boolean)
      .join(', ')
  }

  function selectArticle(article, fly) {
    setSelectedKey(articleKey(article))
    if (!fly || article.lat == null || article.lng == null) return
    mapRef.current?.flyTo({
      center: [article.lng, article.lat],
      zoom: 13,
      duration: 700,
    })
  }

  return (
    <div className="app">
      <aside className="panel">
        <div className="panel-head">
          <div className="panel-head-top">
            <h1 className="site-brand">
              <img
                src="/logo.png"
                alt="YYC Signal"
                className="site-logo"
                width={277}
                height={55}
              />
            </h1>
            <Link to="/about" className="panel-about-link">
              About
            </Link>
          </div>
          <p>
            {filtered.length} of {articles.length} articles
          </p>
        </div>
        <div className="view-toggle" role="tablist" aria-label="Panel view">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'list'}
            className={view === 'list' ? 'active' : undefined}
            onClick={() => setView('list')}
          >
            List
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'stats'}
            className={view === 'stats' ? 'active' : undefined}
            onClick={() => setView('stats')}
          >
            Stats
          </button>
        </div>
        <div className="filters">
          {chips.length > 0 && (
            <div className="chip-row">
              <ul>
                {chips.map((chip) => (
                  <li className="chip" key={`${chip.field}:${chip.value}`}>
                    <span>{chip.label}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${chip.label}`}
                      onClick={() =>
                        dispatch({ type: 'remove', field: chip.field, value: chip.value })
                      }
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
              <button type="button" className="clear-filters" onClick={() => dispatch({ type: 'clear' })}>
                Clear all
              </button>
            </div>
          )}
          <MultiCombobox
            label="Neighborhood"
            options={neighborhoodOptions}
            selected={filters.neighborhoods}
            onToggle={(value) => dispatch({ type: 'toggle', field: 'neighborhoods', value })}
          />
          <MultiCombobox
            label="Quadrant"
            options={quadrantOptions}
            selected={filters.quadrants}
            onToggle={(value) => dispatch({ type: 'toggle', field: 'quadrants', value })}
          />
          <MultiCombobox
            label="Source"
            options={sourceOptions}
            selected={filters.sources}
            onToggle={(value) => dispatch({ type: 'toggle', field: 'sources', value })}
          />
          <MultiCombobox
            label="Landmark"
            options={landmarkChoices}
            selected={filters.landmarks}
            onToggle={(value) => dispatch({ type: 'toggle', field: 'landmarks', value })}
          />
        </div>
        {filtered.length === 0 ? (
          <div className="empty">
            <p>No articles match these filters.</p>
            <button type="button" onClick={() => dispatch({ type: 'clear' })}>
              Clear filters
            </button>
          </div>
        ) : view === 'stats' ? (
          <StatsView stats={stats} />
        ) : (
          <div className="article-scroll">
            {mapped.length > 0 && (
              <ul className="article-list">
                {mapped.map((article) => (
                  <ArticleItem
                    key={articleKey(article)}
                    article={article}
                    selected={articleKey(article) === selectedKey}
                    names={landmarkLabel(article)}
                    onSelect={selectArticle}
                  />
                ))}
              </ul>
            )}
            {offMap.length > 0 && (
              <section className="off-map" aria-label="Not on map">
                <h2>
                  Not on map <span>{offMap.length}</span>
                </h2>
                <ul className="article-list">
                  {offMap.map((article) => (
                    <ArticleItem
                      key={articleKey(article)}
                      article={article}
                      selected={articleKey(article) === selectedKey}
                      names={landmarkLabel(article)}
                      onSelect={selectArticle}
                    />
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </aside>
      <div className="map-wrap">
        {TOKEN && STYLE_URL ? (
          <>
          <MapView
            ref={mapRef}
            mapboxAccessToken={TOKEN}
            initialViewState={{ longitude: -114.0719, latitude: 51.0447, zoom: 10 }}
            mapStyle={STYLE_URL}
            style={{ width: '100%', height: '100%' }}
            onLoad={() => setMapReady(true)}
          >
            {mapped.map((article) => {
              const key = articleKey(article)
              const source = articleSource(article)
              const pinClass =
                key === selectedKey
                  ? `pin pin-${source} selected`
                  : `pin pin-${source}`
              return (
                <Marker
                  key={key}
                  longitude={article.lng}
                  latitude={article.lat}
                  anchor="center"
                  onClick={(event) => {
                    event.originalEvent.stopPropagation()
                    selectArticle(article, false)
                  }}
                >
                  <button type="button" className={pinClass} aria-label={article.title}>
                    <span className="pin-emoji" aria-hidden>
                      {sourceEmoji(source)}
                    </span>
                  </button>
                </Marker>
              )
            })}
            {showLandmarks &&
              catalog
                .filter((landmark) => landmark.lat != null && landmark.lng != null)
                .map((landmark) => (
                  <Marker
                    key={`landmark-${landmark.id}`}
                    longitude={landmark.lng}
                    latitude={landmark.lat}
                    anchor="center"
                    onClick={(event) => {
                      event.originalEvent.stopPropagation()
                      dispatch({ type: 'add', field: 'landmarks', value: landmark.id })
                    }}
                  >
                    <button
                      type="button"
                      className={
                        filters.landmarks.includes(landmark.id)
                          ? 'landmark-pin selected'
                          : 'landmark-pin'
                      }
                      aria-label={landmark.name}
                    />
                  </Marker>
                ))}
            {selected?.lat != null && selected.lng != null && (
              <Popup
                longitude={selected.lng}
                latitude={selected.lat}
                anchor="bottom"
                offset={16}
                closeOnClick={false}
                onClose={() => setSelectedKey(null)}
              >
                <div className="popup">
                  <h2>{selected.title}</h2>
                  <p>
                    {sourceLabel(articleSource(selected))}
                    {' · '}
                    {formatPublished(selected.published)}
                    {selected.neighborhood ? ` · ${placeName(selected.neighborhood)}` : ''}
                    {selected.quadrant ? ` ${selected.quadrant}` : ''}
                  </p>
                  {landmarkLabel(selected) ? <p>{landmarkLabel(selected)}</p> : null}
                  {selected.link ? (
                    <a href={selected.link} target="_blank" rel="noreferrer">
                      Read the release
                    </a>
                  ) : null}
                </div>
              </Popup>
            )}
          </MapView>
          <button
            type="button"
            className="landmark-toggle"
            aria-pressed={showLandmarks}
            onClick={() => setShowLandmarks((value) => !value)}
          >
            Landmarks
          </button>
          </>
        ) : (
          <div className="map-missing">
            <p>Add ACCESS_TOKEN and STYLE_URL to web/.env.local to load the map.</p>
          </div>
        )}
      </div>
    </div>
  )
}

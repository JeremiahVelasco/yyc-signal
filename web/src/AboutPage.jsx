import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import SiteCredits from './SiteCredits'
import './AboutPage.css'

const NEWSROOM = 'https://newsroom.calgary.ca'

export default function AboutPage() {
  useEffect(() => {
    const previous = document.title
    document.title = 'About · YYC Signal'
    return () => {
      document.title = previous
    }
  }, [])

  return (
    <div className="about-page">
      <header className="about-header">
        <Link to="/" className="about-brand">
          <img
            src="/logo.png"
            alt="YYC Signal"
            className="site-logo"
            width={277}
            height={55}
          />
        </Link>
        <Link to="/" className="about-back">
          Back to map
        </Link>
      </header>
      <main className="about-main">
        <h1>About YYC Signal</h1>

        <section>
          <h2>What this is</h2>
          <p>
            YYC Signal is a map-first way to browse public news releases about Calgary. You can
            see where stories mention places, filter by neighborhood or source, and open the
            original release for full details.
          </p>
          <p>
            This is an <strong>independent project</strong>. It is not operated by the City of
            Calgary or{' '}
            <a href={NEWSROOM} target="_blank" rel="noreferrer">
              newsroom.calgary.ca
            </a>
            .
          </p>
        </section>

        <section>
          <h2>Where the news comes from</h2>
          <p>
            Headlines and summaries are pulled from the City’s public newsroom RSS feeds, grouped
            here as Police, Fire, City, and Feature. Every story links to the{' '}
            <strong>canonical release</strong> on{' '}
            <a href={NEWSROOM} target="_blank" rel="noreferrer">
              newsroom.calgary.ca
            </a>
            . For official wording, updates, photos, and corrections, always use that link—not
            this app.
          </p>
        </section>

        <section>
          <h2>How to use the app</h2>
          <ul>
            <li>
              <strong>List</strong> — Browse releases in the sidebar. Select one to highlight it;
              when a pin exists, the map flies to that location.
            </li>
            <li>
              <strong>Map</strong> — Pins mark located stories; icon and color show the source
              (Police 🚔, Fire 🔥, City 🏛️, or Feature 📰). Click a pin or list item to open a popup with a{' '}
              <strong>Read the release</strong> link to the newsroom.
            </li>
            <li>
              <strong>Filters</strong> — Narrow by neighborhood, quadrant (NW/NE/SW/SE), source,
              or landmark. Use <strong>Clear all</strong> to reset. Your filters are stored in the
              page URL so you can bookmark or share a view.
            </li>
            <li>
              <strong>Stats</strong> — See how many articles are on the map, tagged by quadrant,
              linked to landmarks, and how many could not be placed.
            </li>
            <li>
              <strong>Landmarks</strong> — Turn on the Landmarks control on the map to show
              reference points. Click a landmark pin to add it as a filter.
            </li>
          </ul>
        </section>

        <section>
          <h2>How locations are chosen</h2>
          <p>
            Map pins are assigned automatically from the text of each release—street addresses,
            community names, known landmarks, and sometimes a quadrant when nothing more specific
            matches. This is <strong>best-effort tagging</strong>, not official City GIS.
          </p>
          <p>
            Some releases have <strong>no pin</strong> when a location cannot be inferred. The
            Stats view shows how many articles are unlocated.
          </p>
        </section>

        <section>
          <h2>Data freshness</h2>
          <p>
            The site uses a snapshot of articles bundled when the app is built. New newsroom
            releases appear here only after the data is refreshed and the site is redeployed.
          </p>
        </section>

      </main>
      <SiteCredits />
    </div>
  )
}

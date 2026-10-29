import './SiteCredits.css'

const NEWSROOM = 'https://newsroom.calgary.ca'
const SOURCE = 'https://github.com/JeremiahVelasco/yyc-signal'
const CALGARY_OGL = 'https://data.calgary.ca/d/Open-Data-Terms/u45n-7awa'
const OSM_COPYRIGHT = 'https://www.openstreetmap.org/copyright'
const ODBL = 'https://opendatacommons.org/licenses/odbl/1-0/'

export default function SiteCredits() {
  return (
    <footer className="site-credits">
      <p>
        Release titles and summaries belong to their original publishers on{' '}
        <a href={NEWSROOM} target="_blank" rel="noreferrer">
          newsroom.calgary.ca
        </a>
        .
      </p>
      <p>
        Contains information licensed under the{' '}
        <a href={CALGARY_OGL} target="_blank" rel="noreferrer">
          Open Government Licence – City of Calgary
        </a>
        . Geocoding data ©{' '}
        <a href={OSM_COPYRIGHT} target="_blank" rel="noreferrer">
          OpenStreetMap
        </a>{' '}
        contributors (
        <a href={ODBL} target="_blank" rel="noreferrer">
          ODbL
        </a>
        ). Map basemap © Mapbox.
      </p>
      <p>
        <a href={SOURCE} target="_blank" rel="noreferrer">
          Source code
        </a>
      </p>
    </footer>
  )
}

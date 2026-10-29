import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from main import (
    articles_from,
    articles_text,
    combine_feeds,
    lookup_cached_geocode,
    merge_saved,
    persist_merged,
    store_articles,
)


def published_days_ago(days):
    return format_datetime(datetime.now(timezone.utc) - timedelta(days=days))


def article(link, title, days=1, published=None, lat=51.0):
    return {
        "title": title,
        "summary": title,
        "link": link,
        "published": published_days_ago(days) if published is None else published,
        "source": "police",
        "lat": lat,
        "lng": -114.0,
        "neighborhood": "BELTLINE",
        "quadrant": "SW",
        "landmarks": [],
    }


class MergeTests(unittest.TestCase):
    def test_known_link_is_kept(self):
        saved = article("https://newsroom.example/a", "Saved title")
        fetched = article("https://newsroom.example/a", "Feed title", lat=0)
        result = merge_saved([saved], [fetched], now=time.time())
        self.assertEqual(result, [saved])
        self.assertIs(result[0], saved)

    def test_new_link_is_added(self):
        saved = article("https://newsroom.example/saved", "Saved", days=2)
        fresh = article("https://newsroom.example/new", "Fresh", days=0)
        result = merge_saved([saved], [fresh], now=time.time())
        self.assertEqual(
            [item["link"] for item in result],
            [fresh["link"], saved["link"]],
        )
        self.assertIs(result[1], saved)

    def test_old_item_is_dropped(self):
        recent = article("https://newsroom.example/recent", "Recent", days=1)
        old = article("https://newsroom.example/old", "Old", days=31)
        undated = article(
            "https://newsroom.example/undated",
            "Undated",
            published="not a date",
        )
        result = merge_saved([recent, old, undated], [], now=time.time())
        self.assertEqual(
            {item["link"] for item in result},
            {recent["link"], undated["link"]},
        )

    def test_known_link_is_not_tagged_again(self):
        feed = type("Feed", (), {})()
        feed.entries = [
            {
                "title": "Old",
                "summary": "",
                "link": "https://newsroom.example/a",
                "published": published_days_ago(1),
            }
        ]
        known = {"https://newsroom.example/a"}
        self.assertEqual(articles_from(feed, None, None, known_links=known), [])

    def test_partial_failure_keeps_saved_articles(self):
        kept = article("https://newsroom.example/kept", "Kept from failed feed")
        fresh = article("https://newsroom.example/new", "From a live feed", days=0)
        result = combine_feeds([kept], [[fresh]], ["fire"], 4, now=time.time())
        self.assertEqual(
            [item["link"] for item in result],
            [fresh["link"], kept["link"]],
        )
        self.assertIs(result[1], kept)

    def test_unchanged_payload_is_not_written(self):
        articles = [article("https://newsroom.example/a", "Saved")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "articles.json"
            path.write_text(articles_text(articles), encoding="utf-8")
            path.chmod(0o444)
            try:
                self.assertFalse(store_articles(articles, path))
            finally:
                path.chmod(0o644)

    def test_total_failure_does_not_write(self):
        saved = [article("https://newsroom.example/a", "Saved")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "articles.json"
            path.write_text("original\n", encoding="utf-8")
            self.assertIsNone(combine_feeds(saved, [], ["police", "fire", "city", "feature"], 4))
            self.assertFalse(persist_merged(None, path))
            self.assertEqual(path.read_text(encoding="utf-8"), "original\n")

    def test_cache_row_without_display_name_is_a_hit(self):
        self.assertEqual(
            lookup_cached_geocode({"lat": 51.0, "lng": -114.0}),
            (-114.0, 51.0, None),
        )
        self.assertIsNone(lookup_cached_geocode(None))
        self.assertIs(lookup_cached_geocode({"display_name": "incomplete"}), False)


if __name__ == "__main__":
    unittest.main()

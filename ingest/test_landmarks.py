import json
import unittest
from copy import deepcopy

from main import (
    ARTICLES_PATH,
    apply_locations,
    cached_geocoder,
    load_landmarks,
    load_places,
    load_quadrants,
    locate,
    pin_fields,
    validate_article_landmarks,
)
from streets import load_street_index


class LandmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.places, cls.communities = load_places()
        cls.quadrants = load_quadrants()
        cls.streets = load_street_index()
        # A bare function on the class would be bound as a method.
        cls.geocode = staticmethod(cached_geocoder())
        cls.articles = json.loads(ARTICLES_PATH.read_text(encoding="utf-8"))
        cls.catalog = load_landmarks()["landmarks"]

    def tag(self, title, summary=""):
        return locate(
            title,
            summary,
            self.places,
            self.communities,
            self.geocode,
            self.streets,
            self.quadrants,
        )

    def test_frozen_fifty(self):
        known = {landmark["id"] for landmark in self.catalog}
        validate_article_landmarks(self.articles, known)
        articles = deepcopy(self.articles)
        before = [pin_fields(article) for article in articles]
        apply_locations(
            articles,
            self.places,
            self.communities,
            self.geocode,
            self.streets,
            self.quadrants,
        )
        for original, updated, previous in zip(self.articles, articles, before):
            self.assertIsInstance(original["landmarks"], list)
            self.assertEqual(updated["landmarks"], original["landmarks"])
            self.assertEqual(pin_fields(updated), previous)

    def test_foothills_medical_centre_not_the_community(self):
        lat, lng, neighborhood, quadrant, landmarks = self.tag(
            "Incident at Foothills Medical Centre"
        )
        hospital = next(
            landmark
            for landmark in self.catalog
            if landmark["id"] == "foothills-medical-centre"
        )
        self.assertEqual(landmarks, ["foothills-medical-centre"])
        self.assertEqual((lat, lng), (hospital["lat"], hospital["lng"]))
        self.assertEqual((neighborhood, quadrant), ("ST. ANDREWS HEIGHTS", "NW"))
        self.assertNotEqual(neighborhood, "FOOTHILLS")

    def test_address_pin_keeps_landmark_id(self):
        article = next(
            item
            for item in self.articles
            if "Foothills Medical Centre" in item["summary"]
            and "29 Street" in item["summary"]
        )
        lat, lng, neighborhood, quadrant, landmarks = self.tag(
            article["title"], article["summary"]
        )
        self.assertEqual(landmarks, ["foothills-medical-centre"])
        self.assertEqual(
            (lat, lng, neighborhood, quadrant),
            (article["lat"], article["lng"], article["neighborhood"], article["quadrant"]),
        )

    def test_no_landmark(self):
        _lat, _lng, neighborhood, _quadrant, landmarks = self.tag("Crash in Beltline")
        self.assertEqual(landmarks, [])
        self.assertEqual(neighborhood, "BELTLINE")

    def test_alias_matches_canonical_id(self):
        alias = self.tag("Parking at Chinook Centre")
        canonical = self.tag("Parking at CF Chinook Centre")
        self.assertEqual(alias[4], ["cf-chinook-centre"])
        self.assertEqual(canonical[4], ["cf-chinook-centre"])
        self.assertEqual(alias[:4], canonical[:4])

    def test_unknown_id_fails(self):
        with self.assertRaises(RuntimeError) as caught:
            validate_article_landmarks(
                [{"title": "Example", "landmarks": ["not-a-landmark"]}],
                {"foothills-medical-centre"},
            )
        self.assertIn("not-a-landmark", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

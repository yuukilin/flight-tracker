import sqlite3
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


try:
    import fast_flights  # noqa: F401
except ModuleNotFoundError:
    fake_fast_flights = types.ModuleType('fast_flights')
    fake_fast_flights.FlightData = object
    fake_fast_flights.Passengers = object
    fake_fast_flights.get_flights = lambda *args, **kwargs: None
    sys.modules['fast_flights'] = fake_fast_flights


import scripts.query as query
import scripts.scrape as scrape


TEST_ROUTE = {
    'id': 3,
    'name': 'test',
    'origin': 'TPE',
    'destinations': ['CTS'],
    'cabin_classes': ['premium_economy'],
    'depart_date_range': {'start': '2026-10-03', 'end': '2026-10-03'},
    'trip_duration_days': 9,
    'max_stops': 0,
}


def make_conn():
    conn = sqlite3.connect(':memory:')
    conn.execute("""
        CREATE TABLE prices (
            id INTEGER PRIMARY KEY,
            route_id INTEGER,
            scan_ts TEXT,
            depart_date TEXT,
            return_date TEXT,
            days_before_depart INTEGER,
            airline_code TEXT,
            airline_name TEXT,
            flight_no TEXT,
            cabin TEXT,
            is_lcc INTEGER,
            price_twd INTEGER,
            depart_time TEXT,
            arrive_time TEXT,
            stops INTEGER,
            origin TEXT,
            destination TEXT,
            return_depart_time TEXT,
            return_arrive_time TEXT
        )
    """)
    return conn


def run_scrape_with_flights(flights):
    conn = make_conn()
    original_query_one = scrape.query_one
    scrape.query_one = lambda *args, **kwargs: flights
    try:
        written, stats = scrape.scrape_route(TEST_ROUTE, {'GK'}, ['jetstar'], conn, 32)
    finally:
        scrape.query_one = original_query_one
    return written, stats, conn


class BlankAirlineHandlingTest(unittest.TestCase):
    def test_blank_airline_without_verified_time_stays_unclassified(self):
        flight = types.SimpleNamespace(
            price='NT$ 30,000',
            name='',
            departure='',
            arrival='',
            stops=0,
        )

        written, stats, conn = run_scrape_with_flights([flight])

        self.assertEqual(written, 1)
        self.assertEqual(stats.get('source_issue'), 'unclassified_airline')
        self.assertEqual(
            conn.execute('SELECT airline_name, is_lcc, flight_no FROM prices').fetchall(),
            [(scrape.UNCLASSIFIED_AIRLINE_NAME, None, 'unclassified')],
        )

    def test_blank_airline_with_verified_time_uses_known_fallback(self):
        flight = types.SimpleNamespace(
            price='NT$ 30,000',
            name='',
            departure='10:05',
            arrival='15:10',
            stops=0,
        )

        written, stats, conn = run_scrape_with_flights([flight])

        self.assertEqual(written, 1)
        self.assertEqual(stats.get('fallback_written'), 1)
        self.assertIsNone(stats.get('source_issue'))
        self.assertEqual(
            conn.execute('SELECT airline_name, is_lcc, flight_no FROM prices').fetchall(),
            [('STARLUX Airlines', 0, 'JX0850')],
        )

    def test_reclassify_preserves_unclassified_as_null(self):
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE prices (id INTEGER PRIMARY KEY, airline_name TEXT, airline_code TEXT, is_lcc INTEGER)')
        conn.executemany(
            'INSERT INTO prices (airline_name, airline_code, is_lcc) VALUES (?, ?, ?)',
            [
                (scrape.UNCLASSIFIED_AIRLINE_NAME, '', 0),
                ('STARLUX Airlines', 'JX', None),
                ('Jetstar Japan', 'GK', 0),
            ],
        )

        scrape.reclassify_is_lcc(conn, {'GK'}, ['jetstar'])

        self.assertEqual(
            conn.execute('SELECT airline_name, is_lcc FROM prices ORDER BY id').fetchall(),
            [
                (scrape.UNCLASSIFIED_AIRLINE_NAME, None),
                ('STARLUX Airlines', 0),
                ('Jetstar Japan', 1),
            ],
        )

    def test_query_does_not_treat_null_lcc_flag_as_traditional(self):
        self.assertFalse(query.is_traditional_flight(scrape.UNCLASSIFIED_AIRLINE_NAME, '', None, set(), []))
        self.assertTrue(query.is_traditional_flight('STARLUX Airlines', 'JX', 0, set(), []))


if __name__ == '__main__':
    unittest.main()

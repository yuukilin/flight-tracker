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


def run_scrape_with_query(fake_query_one):
    conn = make_conn()
    original_query_one = scrape.query_one
    scrape.query_one = fake_query_one
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

    def test_no_direct_cabin_results_keeps_relaxed_stop_diagnostics(self):
        one_stop_flight = types.SimpleNamespace(
            price='NT$ 31,036',
            name='Cathay Pacific',
            departure='9:15 PM',
            arrival='2:50 PM',
            stops=1,
        )

        def fake_query_one(origin, dest, depart_date, return_date, cabin, diagnostics=None, max_stops=None):
            if diagnostics is not None:
                diagnostics['query_count'] += 1
            if max_stops == 0:
                if diagnostics is not None:
                    diagnostics['no_result_examples'].append({
                        'origin': origin,
                        'destination': dest,
                        'depart_date': depart_date.isoformat(),
                        'return_date': return_date.isoformat(),
                        'cabin': cabin,
                    })
                return []
            if diagnostics is not None:
                diagnostics['raw_flights'] += 1
            return [one_stop_flight]

        written, stats, conn = run_scrape_with_query(fake_query_one)

        self.assertEqual(written, 0)
        self.assertEqual(stats.get('source_issue'), 'no_direct_cabin_results')
        self.assertEqual(stats['relaxed_max_stops']['raw_flights'], 1)
        self.assertEqual(stats['relaxed_max_stops']['direct_flights'], 0)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM prices').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()

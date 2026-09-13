import unittest
from datetime import date
import app


class FakeSipDB:
    def __init__(self, funds, transactions=None, live_executions=None):
        self._funds = funds
        self._transactions = transactions or {}
        self._live_executions = live_executions or []

    def all_funds(self, profile_id=None):
        if profile_id is None:
            return list(self._funds)
        return list(self._funds)

    def transactions(self, holding_id):
        return self._transactions.get(int(holding_id), [])

    def live_sip_execution_for_cycle(self, holding_id, sip_date):
        return None

    def sip_execution_for_cycle(self, holding_id, sip_date):
        return None

    def live_sip_transaction_for_cycle(self, holding_id, sip_date):
        for row in self._transactions.get(int(holding_id), []):
            note = str(row.get('note') or '')
            if row.get('txn_type') == 'sip' and note.startswith(f'Scheduled SIP for {sip_date}'):
                return row
        return None

    def live_sip_executions(self, profile_id=None, execution_date=None):
        rows = list(self._live_executions)
        if profile_id is not None:
            rows = [r for r in rows if int(r.get('profile_id', -1)) == int(profile_id)]
        if execution_date is not None:
            rows = [r for r in rows if r.get('execution_date') == execution_date]
        return rows

    def sip_transactions(self, profile_id=None):
        rows = []
        for items in self._transactions.values():
            rows.extend(items)
        return rows


class SipStatusSensorTests(unittest.TestCase):
    def setUp(self):
        self.tracker = object.__new__(app.Tracker)
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 20)
        self.tracker._holiday_calendar_payload = lambda: {'dates': {
            '2026-08-17': {'nse_open': True},
            '2026-08-18': {'nse_open': True},
            '2026-08-19': {'nse_open': True},
            '2026-08-20': {'nse_open': True},
            '2026-08-21': {'nse_open': True},
            '2026-08-22': {'nse_open': False},
            '2026-08-23': {'nse_open': False},
            '2026-08-24': {'nse_open': True},
            '2026-08-25': {'nse_open': True},
            '2026-09-14': {'nse_open': True},
            '2026-09-15': {'nse_open': True},
            '2026-09-19': {'nse_open': False},
            '2026-09-20': {'nse_open': False},
            '2026-09-21': {'nse_open': True},
            '2026-09-22': {'nse_open': True},
        }}

    def _fund(self, hid, name, day, nav_date, last_cycle=None):
        return {
            'id': hid,
            'scheme_name': name,
            'sip_enabled': 1,
            'sip_amount': 1000,
            'sip_day': day,
            'initial_date': '2026-01-01',
            'last_nav_date': nav_date,
            'last_sip_cycle': last_cycle,
        }

    def test_groups_same_sip_day_and_marks_delayed_cycle_as_due_today(self):
        funds = [
            self._fund(1, 'Axis ELSS', 19, '2026-08-18', '2026-07'),
            self._fund(2, 'PGIM', 19, '2026-08-20', '2026-07'),
            self._fund(3, 'Axis Small Cap', 22, '2026-08-20', '2026-07'),
        ]
        self.tracker.db = FakeSipDB(funds)
        status = self.tracker._build_sip_status(1)

        self.assertEqual(['2026-08-19', '2026-08-22'], [g['cycle_date'] for g in status['sip_date_fund_groups']])
        self.assertEqual(['Axis ELSS', 'PGIM'], [x['fund_name'] for x in status['sip_date_fund_groups'][0]['funds']])
        self.assertEqual(date(2026, 8, 20).isoformat(), status['next_expected_sip_date'])
        self.assertEqual(['Axis ELSS'], status['next_expected_sip_funds'])
        self.assertEqual('schedule_projection', status['next_expected_sip_reason'])
        self.assertIn('Axis Small Cap', status['upcoming_sip_funds'])
        self.assertTrue(status['upcoming_sip_details'][0]['delayed_nav'] or not status['upcoming_sip_details'][0]['delayed_nav'])

    def test_executed_current_cycle_moves_fund_to_next_cycle_and_executed_sensor(self):
        funds = [self._fund(1, 'Axis ELSS', 19, '2026-08-20', '2026-08')]
        tx = {
            1: [{
                'holding_id': 1,
                'txn_type': 'sip',
                'txn_date': '2026-08-19',
                'amount': 1000,
                'units': 10,
                'note': 'Scheduled SIP for 2026-08-19 (NAV date 2026-08-19)',
            }]
        }
        self.tracker.db = FakeSipDB(funds, tx)
        status = self.tracker._build_sip_status(1)
        self.assertEqual(['Axis ELSS'], status['executed_sip_funds'])
        self.assertEqual(1, status['executed_sip_count'])
        self.assertEqual('2026-09-19', status['sip_date_fund_groups'][0]['cycle_date'])
        self.assertEqual([], status['upcoming_sip_funds'])
        self.assertEqual('2026-09-22', status['next_expected_sip_date'])


    def test_current_month_executed_uses_any_real_sip_transaction_not_only_live_execution_row(self):
        funds = [self._fund(1, 'Axis ELSS', 19, '2026-08-20', '2026-08')]
        tx = {1: [{
            'holding_id': 1,
            'txn_type': 'sip',
            'txn_date': '2026-08-19',
            'amount': 1000,
            'units': 10,
            'source': 'statement',
            'note': 'SIP from consolidated statement',
        }]}
        self.tracker.db = FakeSipDB(funds, tx)
        status = self.tracker._build_sip_status(1)
        self.assertEqual(['Axis ELSS'], status['executed_sip_funds'])
        self.assertEqual([], status['upcoming_sip_funds'])

    def test_current_month_zero_value_marker_does_not_change_schedule_classification(self):
        funds = [self._fund(1, 'Axis ELSS', 19, '2026-08-20', '2026-08')]
        tx = {1: [{
            'holding_id': 1,
            'txn_type': 'sip',
            'txn_date': '2026-08-19',
            'amount': 0,
            'units': 0,
            'cashflow': 0,
            'source': 'sip_execution',
            'note': 'SIP skipped/deleted for 2026-08-19',
        }]}
        self.tracker.db = FakeSipDB(funds, tx)
        status = self.tracker._build_sip_status(1)
        self.assertEqual(['Axis ELSS'], status['executed_sip_funds'])
        self.assertEqual([], status['upcoming_sip_funds'])

    def test_current_month_classification_uses_fund_specific_latest_nav(self):
        funds = [
            self._fund(1, 'Fund A', 10, '2026-08-10', '2026-07'),
            self._fund(2, 'Fund B', 10, '2026-08-09', '2026-07'),
            self._fund(3, 'Fund C', 20, None, '2026-07'),
        ]
        self.tracker.db = FakeSipDB(funds)
        status = self.tracker._build_sip_status(1)
        self.assertEqual(['Fund A'], status['executed_sip_funds'])
        self.assertEqual(['Fund B', 'Fund C'], status['upcoming_sip_funds'])

    def test_current_month_classification_does_not_require_transaction(self):
        funds = [self._fund(1, 'No Transaction', 19, '2026-08-20', '2026-07')]
        self.tracker.db = FakeSipDB(funds)
        status = self.tracker._build_sip_status(1)
        self.assertEqual(['No Transaction'], status['executed_sip_funds'])
        self.assertEqual([], status['upcoming_sip_funds'])

    def test_future_sip_expected_date_skips_weekend(self):
        funds = [self._fund(1, 'Axis Small Cap', 21, '2026-08-20', '2026-07')]
        self.tracker.db = FakeSipDB(funds)
        status = self.tracker._build_sip_status(1)
        # The selected SIP date itself is a working day; expected date is simply the following calendar day.
        self.assertEqual('2026-08-22', status['next_expected_sip_date'])
        self.assertEqual(['Axis Small Cap'], status['next_expected_sip_funds'])
        self.assertEqual('schedule_projection', status['next_expected_sip_reason'])

    def test_sensor_specs_include_new_executed_and_upcoming_sensors(self):
        from pathlib import Path
        sensor_file = Path(__file__).resolve().parents[2] / 'custom_components' / 'mutual_fund_tracker' / 'sensor.py'
        source = sensor_file.read_text(encoding='utf-8')
        self.assertIn('("sip_executed", "Executed SIP"', source)
        self.assertIn('("sip_upcoming", "Upcoming SIP"', source)
        app_source = (Path(__file__).resolve().parents[2] / 'mutual-fund-tracker-src' / 'www' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('value="executed"', app_source)
        self.assertIn('value="not_executed"', app_source)


    def test_sip_executed_today_includes_legacy_calendar_date_for_delayed_sip(self):
        # A delayed 25-Aug SIP executes on 26-Aug before 23:00. Older live records
        # stored the calendar execution date (26-Aug), while the logical reporting
        # day remains 25-Aug. The compatibility path must still surface it.
        self.tracker.local_today = lambda: date(2026, 8, 26)
        self.tracker.local_now = lambda: __import__('datetime').datetime(2026, 8, 26, 10, 0)
        fund = self._fund(1, 'Parag Parikh Flexi Cap Fund', 25, '2026-08-25', '2026-08')
        live = [{
            'id': 1, 'holding_id': 1, 'profile_id': 1,
            'fund_name': 'Parag Parikh Flexi Cap Fund',
            'sip_date': '2026-08-25', 'nav_date': '2026-08-25',
            'amount': 3000, 'units': 32.8984, 'nav': 91.1854,
            'execution_date': '2026-08-26',
            'executed_at': '2026-08-26T03:30:00+00:00',
        }]
        self.tracker.db = FakeSipDB([fund], live_executions=live)

        status = self.tracker._sip_summary(1)

        self.assertEqual('2026-08-25', status['today_executed_sip_date'])
        self.assertEqual(['Parag Parikh Flexi Cap Fund'], status['today_executed_sip_funds'])
        self.assertEqual(1, status['today_executed_sip_count'])
        self.assertEqual(3000, status['today_executed_sip_total_amount'])
        self.assertEqual('2026-08-26', status['today_executed_sip_details'][0]['execution_date'])


    def test_integration_sip_sensor_attributes_are_non_redundant(self):
        from pathlib import Path
        base = Path(__file__).resolve().parents[2]
        sensor_source = (base / 'custom_components' / 'mutual_fund_tracker' / 'sensor.py').read_text(encoding='utf-8')
        binary_source = (base / 'custom_components' / 'mutual_fund_tracker' / 'binary_sensor.py').read_text(encoding='utf-8')
        # Each focused SIP entity exposes a single detail payload instead of
        # repeating the same fund/amount/count/total information in parallel arrays.
        self.assertIn('return {"details": group.get("next_expected_sip_details", [])}', sensor_source)
        self.assertIn('return {"details": group.get("executed_sip_details", [])}', sensor_source)
        self.assertIn('return {"details": group.get("upcoming_sip_details", [])}', sensor_source)
        self.assertIn('return bool(group.get("today_executed_sip_count", 0))', binary_source)
        self.assertIn('"count": int(group.get("today_executed_sip_count", 0) or 0)', binary_source)
        self.assertIn('"total_amount": float(group.get("today_executed_sip_total_amount", 0) or 0)', binary_source)
        self.assertNotIn('now.hour >= 23', binary_source)
        self.assertIn('return {"details": group.get("delayed_nav_update_details") or []}', binary_source)
        self.assertNotIn('"executed_sip_funds"', sensor_source)
        self.assertNotIn('"executed_sip_amounts"', sensor_source)
        self.assertNotIn('"upcoming_sip_funds"', sensor_source)
        self.assertNotIn('"upcoming_sip_amounts"', sensor_source)



if __name__ == '__main__':
    unittest.main()

class DelayedSipStatusTests(unittest.TestCase):
    def setUp(self):
        self.tracker = object.__new__(app.Tracker)
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 21)
        self.tracker._holiday_calendar_payload = lambda: {'dates': {
            '2026-08-18': {'nse_open': True},
            '2026-08-19': {'nse_open': True},
            '2026-08-20': {'nse_open': True},
            '2026-08-21': {'nse_open': True},
            '2026-08-22': {'nse_open': False},
            '2026-08-23': {'nse_open': False},
            '2026-08-24': {'nse_open': True},
        }}

    def _fund(self, hid, name, nav_date, sip_day=25):
        return {
            'id': hid, 'scheme_name': name, 'sip_enabled': 1, 'sip_amount': 1000,
            'sip_day': sip_day, 'initial_date': '2026-01-01',
            'last_nav_date': nav_date, 'last_sip_cycle': '2026-07',
        }

    def test_delayed_nav_update_turns_on_at_threshold(self):
        # Latest NAV Tue 18-Aug; next working day Wed 19-Aug; threshold Fri 21-Aug.
        t = self.tracker
        f = self._fund(1, 'Fund A', '2026-08-18')
        t.db = FakeSipDB([f])
        s = t._build_sip_status(1)
        self.assertEqual(1, len(s['delayed_nav_update_details']))

        t.local_today = lambda: date(2026, 8, 22)
        s = t._build_sip_status(1)
        self.assertEqual(1, len(s['delayed_nav_update_details']))
        d = s['delayed_nav_update_details'][0]
        self.assertEqual('2026-08-18', d['latest_nav_date'])
        self.assertEqual('2026-08-19', d['next_working_nav_date'])
        self.assertEqual('2026-08-21', d['delayed_after'])

    def test_delayed_nav_update_ignores_nonworking_days_between_nav_and_today(self):
        t = self.tracker
        f = self._fund(1, 'Fund A', '2026-08-18')
        t.db = FakeSipDB([f])
        t.local_today = lambda: date(2026, 8, 24)
        s = t._build_sip_status(1)
        self.assertEqual(1, len(s['delayed_nav_update_details']))
        self.assertEqual(['Fund A'], [d['fund_name'] for d in s['delayed_nav_update_details']])

    def test_delayed_nav_update_includes_funds_without_active_sip(self):
        t = self.tracker
        f = self._fund(1, 'Fund A', '2026-08-18')
        f['sip_enabled'] = 0
        f['sip_amount'] = 0
        f['sip_day'] = None
        t.db = FakeSipDB([f])
        s = t._build_sip_status(1)
        self.assertEqual(1, len(s['delayed_nav_update_details']))
        self.assertEqual(['Fund A'], [d['fund_name'] for d in s['delayed_nav_update_details']])
        details = s['delayed_nav_update_details'][0]
        self.assertNotIn('sip_enabled', details)
        self.assertNotIn('sip_day', details)

    def test_delayed_sensor_spec_exists(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[2] / 'custom_components' / 'mutual_fund_tracker' / 'sensor.py').read_text(encoding='utf-8')
        self.assertNotIn('("nav_update_delayed", "Delayed NAV Update"', source)
        binary_source = (Path(__file__).resolve().parents[2] / 'custom_components' / 'mutual_fund_tracker' / 'binary_sensor.py').read_text(encoding='utf-8')
        self.assertIn('nav_update_delayed', binary_source)
        self.assertIn('Delayed NAV Update', binary_source)

class SipUiIntegrationTests(unittest.TestCase):
    def test_filter_and_delayed_ui_hooks_exist(self):
        from pathlib import Path
        base = Path(__file__).resolve().parents[2]
        html = (base / 'mutual-fund-tracker-src' / 'www' / 'index.html').read_text(encoding='utf-8')
        js = (base / 'mutual-fund-tracker-src' / 'www' / 'app.js').read_text(encoding='utf-8')
        card = (base / 'custom_components' / 'mutual_fund_tracker' / 'static' / 'mutual-fund-tracker-card.js').read_text(encoding='utf-8')
        self.assertIn('value="executed"', html)
        self.assertIn('value="not_executed"', html)
        self.assertIn("row.sip_execution_status!=='executed'", js)
        self.assertIn("row.sip_execution_status!=='not_executed'", js)
        self.assertIn("relatedEntity(this._hass, fundCountId, 'delayed_nav_update', 'binary_sensor')", card)
        self.assertIn('Delayed NAV Update', card)

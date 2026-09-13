import unittest
from datetime import date
import app


class HolidayAndNextSipTests(unittest.TestCase):
    def setUp(self):
        self.tracker = object.__new__(app.Tracker)
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker._calendar = {
            '2026-08-14': {'nse_open': True},
            '2026-08-15': {'nse_open': False, 'type': 'WEEKEND', 'description': 'Saturday'},
            '2026-08-16': {'nse_open': False, 'type': 'WEEKEND', 'description': 'Sunday'},
            '2026-08-17': {'nse_open': True},
            '2026-08-18': {'nse_open': True},
            '2026-08-19': {'nse_open': True},
            '2026-08-20': {'nse_open': True},
            '2026-08-21': {'nse_open': True},
        }
        self.tracker.local_today = lambda: date(2026, 8, 16)
        self.tracker._holiday_calendar_payload = lambda: {'year': 2026, 'dates': self.tracker._calendar}

    def test_aug_16_is_holiday_and_aug_17_next_trading_day(self):
        status = self.tracker._holiday_status(date(2026, 8, 16))
        self.assertFalse(status['nse_open'])
        self.assertEqual(status['next_trading_date'], '2026-08-17')

    def test_latest_nav_date_controls_pending_sip_cycle(self):
        self.tracker.local_today = lambda: date(2026, 8, 16)
        class DB:
            def all_funds(self, _):
                return [
                    {'id': 1, 'sip_enabled': 1, 'sip_amount': 1000, 'sip_day': 15, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-14', 'scheme_name': 'Fund 15'},
                ]
        self.tracker.db = DB()
        result = self.tracker._next_expected_sip(1)
        self.assertEqual(result[0], date(2026, 8, 18))
        self.assertEqual(result[1], ['Fund 15'])
        self.assertEqual(result[3], '2026-08-17')

    def test_holiday_window_includes_sip_dates_after_latest_nav(self):
        class DB:
            def all_funds(self, _):
                return [
                    {'id': 1, 'sip_enabled': 1, 'sip_amount': 2500, 'sip_day': 22, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-21', 'scheme_name': 'Axis 22'},
                    {'id': 2, 'sip_enabled': 1, 'sip_amount': 3000, 'sip_day': 23, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-21', 'scheme_name': 'Quant 23'},
                    {'id': 3, 'sip_enabled': 1, 'sip_amount': 2500, 'sip_day': 24, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-21', 'scheme_name': 'PGIM 24'},
                    {'id': 4, 'sip_enabled': 1, 'sip_amount': 1500, 'sip_day': 21, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-21', 'scheme_name': 'Already 21'},
                ]
        self.tracker.local_today = lambda: date(2026, 8, 23)
        self.tracker._calendar.update({
            '2026-08-21': {'nse_open': True},
            '2026-08-22': {'nse_open': False, 'type': 'WEEKEND'},
            '2026-08-23': {'nse_open': False, 'type': 'WEEKEND'},
            '2026-08-24': {'nse_open': True},
            '2026-08-25': {'nse_open': True},
        })
        self.tracker.db = DB()
        result = self.tracker._next_expected_sip(1)
        self.assertEqual(result[0], date(2026, 8, 25))
        self.assertEqual(result[1], ['Axis 22', 'PGIM 24', 'Quant 23'])
        self.assertEqual(result[3], '2026-08-24')

    def test_holiday_first_sip_date_batches_through_next_working_day(self):
        class DB:
            def all_funds(self, _):
                return [
                    {'id': 1, 'sip_enabled': 1, 'sip_amount': 1000, 'sip_day': 23, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-21', 'scheme_name': 'Fund 23'},
                    {'id': 2, 'sip_enabled': 1, 'sip_amount': 1500, 'sip_day': 24, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-21', 'scheme_name': 'Fund 24'},
                    {'id': 3, 'sip_enabled': 1, 'sip_amount': 2000, 'sip_day': 25, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-21', 'scheme_name': 'Fund 25'},
                ]
        self.tracker.local_today = lambda: date(2026, 8, 23)
        self.tracker._calendar.update({
            '2026-08-22': {'nse_open': False, 'type': 'WEEKEND'},
            '2026-08-23': {'nse_open': False, 'type': 'WEEKEND'},
            '2026-08-24': {'nse_open': True},
            '2026-08-25': {'nse_open': True},
        })
        self.tracker.db = DB()
        result = self.tracker._next_expected_sip(1)
        self.assertEqual(result[0], date(2026, 8, 25))
        self.assertEqual(result[1], ['Fund 23', 'Fund 24'])
        self.assertEqual(result[3], '2026-08-24')

    def test_working_first_sip_date_does_not_skip_holiday_on_following_day(self):
        class DB:
            def all_funds(self, _):
                return [
                    {'id': 1, 'sip_enabled': 1, 'sip_amount': 1000, 'sip_day': 21, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-20', 'scheme_name': 'Fund 21'},
                    {'id': 2, 'sip_enabled': 1, 'sip_amount': 1500, 'sip_day': 22, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-20', 'scheme_name': 'Fund 22'},
                ]
        self.tracker.local_today = lambda: date(2026, 8, 20)
        self.tracker._calendar.update({
            '2026-08-21': {'nse_open': True},
            '2026-08-22': {'nse_open': False, 'type': 'WEEKEND'},
            '2026-08-23': {'nse_open': False, 'type': 'WEEKEND'},
        })
        self.tracker.db = DB()
        result = self.tracker._next_expected_sip(1)
        self.assertEqual(result[0], date(2026, 8, 22))
        self.assertEqual(result[1], ['Fund 21'])
        self.assertEqual(result[3], '2026-08-21')

    def test_sip_summary_serializes_next_expected_sip_date(self):
        class DB:
            def all_funds(self, _):
                return [
                    {'sip_enabled': 1, 'sip_amount': 1000, 'sip_day': 15, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-14', 'scheme_name': 'Fund 15'},
                ]
            def sip_executions(self, _):
                return []
        self.tracker.db = DB()
        result = self.tracker._sip_summary(1)
        self.assertEqual(result['next_expected_sip_date'], '2026-08-18')

    def test_no_funds_by_aug_17_advances_to_next_sip_day(self):
        class DB:
            def all_funds(self, _):
                return [
                    {'sip_enabled': 1, 'sip_amount': 1000, 'sip_day': 20, 'last_sip_cycle': None, 'initial_date': None, 'last_nav_date': '2026-08-14', 'scheme_name': 'Fund 20'},
                ]
        self.tracker.db = DB()
        result = self.tracker._next_expected_sip(1)
        self.assertEqual(result[0], date(2026, 8, 21))
        self.assertEqual(result[1], ['Fund 20'])
        self.assertEqual(result[3], '2026-08-20')


if __name__ == '__main__':
    unittest.main()

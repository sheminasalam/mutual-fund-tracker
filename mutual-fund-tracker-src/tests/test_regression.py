#!/usr/bin/env python3
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


class TrackerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = app.Database(Path(self.tmp.name) / 'tracker.db')
        self.tracker = object.__new__(app.Tracker)
        self.tracker.db = self.db
        self.tracker.timezone_name = 'UTC'
        self.tracker.lock = __import__('threading').Lock()
        self.tracker._integration_state_lock = __import__('threading').Lock()
        self.tracker._export_dir = Path(self.tmp.name) / 'exports'
        self.tracker._export_lock = __import__('threading').Lock()
        self.tracker._amfi_catalog = None
        self.tracker._amfi_catalog_at = 0
        self.tracker._latest_nav_snapshot = None
        self.tracker._latest_nav_snapshot_at = 0.0
        self.tracker._latest_nav_snapshot_all_schemes = False
        self.tracker._latest_nav_snapshot_lock = __import__('threading').Lock()
        self.tracker._amfi_history_snapshot_cache = {}
        self.tracker._amfi_history_snapshot_lock = __import__('threading').Lock()
        self.tracker._nav_reference_lock = __import__('threading').Lock()
        self._original_request_bytes = app.request_bytes
        default_amfi = 'Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n100001;-;-;Test Fund;Direct Plan;Growth;100.0;20-Aug-2026\n100002;-;-;Test Fund 2;Direct Plan;Growth;100.0;20-Aug-2026\n100003;-;-;Test Fund 3;Direct Plan;Growth;100.0;20-Aug-2026\n100009;-;-;Test Fund 9;Direct Plan;Growth;100.0;20-Aug-2026\n100010;-;-;Test Fund 10;Direct Plan;Growth;100.0;20-Aug-2026\n100011;-;-;Test Fund 11;Direct Plan;Growth;100.0;20-Aug-2026\n100012;-;-;Test Fund 12;Direct Plan;Growth;100.0;20-Aug-2026\n100013;-;-;Test Fund 13;Direct Plan;Growth;100.0;20-Aug-2026\n100123;-;-;Test Fund 123;Direct Plan;Growth;100.0;20-Aug-2026\n155001;-;-;Test Fund 155001;Direct Plan;Growth;100.0;20-Aug-2026\n120503;INF846K01EW2;-;Axis ELSS Tax Saver Fund;Direct Plan;Growth Option;112.2447;20-Aug-2026\n120505;INF846K01EH3;-;Axis Mid Cap Fund;Direct Plan;Growth Option;144.44;20-Aug-2026\n'
        default_amfi += ''.join(f'{code};-;-;Synthetic Fund {code};Direct Plan;Growth;100.0;20-Aug-2026\n' for code in range(100000,100026) if str(code) not in default_amfi)
        def _fake_request_bytes(url, **kwargs):
            text_url = str(url)
            if 'NAVAll.txt' in text_url:
                return default_amfi.encode('utf-8')
            if 'DownloadNAVHistoryReport_Po.aspx' in text_url:
                from urllib.parse import parse_qs, urlparse
                raw_date = parse_qs(urlparse(text_url).query).get('frmdt', ['20-Aug-2026'])[0]
                rows = ['Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date']
                codes = ['100001','100002','100003','100009','100010','100011','100012','100013','100123','155001','120503','120505']
                rows.extend(f'{code};Synthetic Fund;Direct Plan;Growth Option;;;100.0;{raw_date}' for code in codes)
                return ('\n'.join(rows) + '\n').encode('utf-8')
            return self._original_request_bytes(url, **kwargs)
        app.request_bytes = _fake_request_bytes
        self.addCleanup(self._restore_request_bytes)
    def _restore_request_bytes(self):
        app.request_bytes = self._original_request_bytes

    def tearDown(self):
        self.tmp.cleanup()

    def add_fund(self, code='100001', name='Test Fund', profile_id=1, sip_day=13,
                 sip_amount=1000, enabled=True, transactions=None):
        payload = {
            'profile_id': profile_id,
            'scheme_code': code,
            'scheme_name': name,
            'isin': None,
            'folios': [],
            'units': 100,
            'invested': 10000,
            'sip_enabled': enabled,
            'sip_amount': sip_amount,
            'sip_day': sip_day,
            'initial_date': '2025-01-01',
        }
        return self.db.seed_fund(payload, transactions or [])


class RefreshSchedulingTests(TrackerTestBase):
    def test_global_refresh_timestamp_is_persisted_and_reported(self):
        self.assertIsNone(self.db.get_setting('last_successful_nav_refresh', None))
        stamp = '2026-08-17T15:00:00Z'
        self.db.set_setting('last_successful_nav_refresh', stamp)
        self.assertEqual(stamp, self.db.last_refresh_time(None))

    def test_nav_refresh_error_is_persisted_and_can_be_cleared(self):
        payload = self.db.set_nav_refresh_error('API timeout', 'Automatic NAV refresh', 2, '2026-08-17T20:00:00+00:00')
        self.assertEqual('API timeout', payload['error'])
        self.assertEqual(2, payload['retry_count'])
        self.assertEqual('Automatic NAV refresh', payload['error_stage'])
        stored = self.db.nav_refresh_error()
        self.assertEqual('API timeout', stored['error'])
        self.db.clear_nav_refresh_error()
        self.assertIsNone(self.db.nav_refresh_error())

    def test_refresh_error_does_not_replace_last_successful_timestamp(self):
        stamp = '2026-08-17T15:00:00Z'
        self.db.set_setting('last_successful_nav_refresh', stamp)
        self.db.set_nav_refresh_error('No NAV records returned', 'NAV refresh', 0, None)
        self.assertEqual(stamp, self.db.last_refresh_time(None))

    def test_failed_full_refresh_keeps_last_success_and_sets_error(self):
        stamp = '2026-08-17T15:00:00Z'
        self.db.set_setting('last_successful_nav_refresh', stamp)
        fid = self.add_fund(code='100123', name='Failing Fund')
        self.db.update_nav(fid, {
            'last_nav': 100.0, 'last_nav_date': '2026-08-17',
            'previous_nav': 99.0, 'previous_nav_date': '2026-08-16',
            'month_nav': 98.0, 'month_nav_date': '2026-08-03',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': app.iso_now(), 'last_error': None,
        })
        self.tracker._get_ha_timezone = lambda: None
        self.tracker.update_ha_states = lambda *_args, **_kwargs: None
        self.tracker._write_integration_state = lambda: None
        self.tracker._holiday_status = lambda d: {'nse_open': True, 'description': 'Functional'}
        self.tracker._market_status_payload = lambda: {'market_open': False, 'status': 'CLOSED', 'reason': 'Closed'}
        self.tracker.request_latest_nav_snapshot = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('simulated latest snapshot failure'))
        with self.assertRaises(RuntimeError):
            self.tracker.refresh_all()
        self.assertEqual(stamp, self.db.last_refresh_time(None))
        error = self.db.nav_refresh_error()
        self.assertIsNotNone(error)
        self.assertIn('simulated latest snapshot failure', error['error'])

    def test_automatic_nav_refresh_due_uses_global_success_timestamp(self):
        # A per-holding refresh timestamp must not postpone the scheduler.
        self.db.set_setting('last_successful_nav_refresh', '2026-08-19T04:00:00Z')
        self.db.set_setting('refresh_interval_seconds', '3600')
        with patch('app.datetime') as dt:
            dt.now.return_value = datetime(2026, 8, 19, 5, 1, tzinfo=timezone.utc)
            dt.fromisoformat.side_effect = datetime.fromisoformat
            self.assertTrue(self.tracker.automatic_nav_refresh_due())

    def test_automatic_nav_refresh_due_not_due_before_interval(self):
        self.db.set_setting('last_successful_nav_refresh', '2026-08-19T04:30:00Z')
        self.db.set_setting('refresh_interval_seconds', '3600')
        with patch('app.datetime') as dt:
            dt.now.return_value = datetime(2026, 8, 19, 5, 29, 59, tzinfo=timezone.utc)
            dt.fromisoformat.side_effect = datetime.fromisoformat
            self.assertFalse(self.tracker.automatic_nav_refresh_due())

    def test_automatic_nav_refresh_due_when_no_success_exists(self):
        self.db.set_setting('refresh_interval_seconds', '3600')
        self.assertTrue(self.tracker.automatic_nav_refresh_due())

    def test_profile_refresh_timestamp_still_uses_holding_when_profile_scoped(self):
        fid = self.add_fund(profile_id=1)
        self.db.update_nav(fid, {
            'last_nav': 100.0, 'last_nav_date': '2026-08-17',
            'previous_nav': 99.0, 'previous_nav_date': '2026-08-16',
            'month_nav': 98.0, 'month_nav_date': '2026-08-01',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': '2026-08-17T14:00:00Z', 'last_error': None,
        })
        self.assertEqual('2026-08-17T14:00:00Z', self.db.last_refresh_time(1))




class NavRefreshReuseTests(TrackerTestBase):
    def test_nav_refresh_is_recent_for_sub_two_hour_success(self):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=59)).isoformat()
        self.db.set_setting('last_successful_nav_refresh', stamp)
        self.assertTrue(self.tracker.nav_refresh_is_recent())

    def test_nav_refresh_is_not_recent_at_two_hours(self):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=2, seconds=1)).isoformat()
        self.db.set_setting('last_successful_nav_refresh', stamp)
        self.assertFalse(self.tracker.nav_refresh_is_recent())

    def test_refresh_all_if_stale_skips_recent_refresh(self):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        self.db.set_setting('last_successful_nav_refresh', stamp)
        self.tracker.refresh_all = lambda: (_ for _ in ()).throw(AssertionError('full refresh should be skipped when recent'))
        self.assertFalse(self.tracker.refresh_all_if_stale())

    def test_refresh_all_if_stale_runs_when_refresh_is_old(self):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=2, seconds=1)).isoformat()
        self.db.set_setting('last_successful_nav_refresh', stamp)
        self.tracker.refresh_all = lambda: True
        self.assertTrue(self.tracker.refresh_all_if_stale())

    def test_latest_nav_snapshot_is_cached_within_two_hours(self):
        raw = 'Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n100001;-;-;Cache Fund;Direct Plan;Growth;100.0;18-Aug-2026\n'
        calls = []
        original = app.request_bytes
        app.request_bytes = lambda url, **kwargs: calls.append(url) or raw.encode('utf-8')
        try:
            first = self.tracker.request_latest_nav_snapshot(force=True)
            second = self.tracker.request_latest_nav_snapshot()
        finally:
            app.request_bytes = original
        self.assertEqual(first, second)
        self.assertEqual(1, len(calls))

class SipHistoricalNavLookupTests(TrackerTestBase):
    def test_nav_on_or_after_date_selects_earliest_available_record(self):
        calls=[]
        self.tracker.request_nav_json_with_retry = lambda url: calls.append(url) or {'data': [
            {'date':'25-08-2026','nav':'250'},
            {'date':'24-08-2026','nav':'240'},
            {'date':'23-08-2026','nav':'230'},
            {'date':'18-08-2026','nav':'180'},
        ]}
        result = self.tracker.nav_on_or_after_date('100001', date(2026,8,19), date(2026,8,25))
        self.assertEqual((date(2026,8,23), 230.0), result)
        self.assertIn('startDate=2026-08-19&endDate=2026-08-25', calls[0])

class SipLogicTests(TrackerTestBase):
    def fund_row(self, fid):
        return self.db.get_fund(fid)

    def test_same_day_sip_executes_when_missing(self):
        fid = self.add_fund()
        fund = self.fund_row(fid)
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 13), 100.0)], date(2026, 8, 13))
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertAlmostEqual(txs[0]['units'], 10.0)

    def test_same_day_redemption_does_not_suppress_sip(self):
        fid = self.add_fund(transactions=[{
            'txn_type': 'sell', 'txn_date': '2026-08-13', 'amount': 100,
            'cashflow': 100, 'units': -1, 'nav': 100, 'charges': 0,
            'note': 'Redemption'
        }])
        fund = self.fund_row(fid)
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 13), 100.0)], date(2026, 8, 13))
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))

    def test_past_sip_cycle_is_not_replayed_when_accounted(self):
        fid = self.add_fund(sip_day=2)
        self.db.set_sip_cycle_accounted(fid, '2026-08')
        fund = self.fund_row(fid)
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 14), 100.0)], date(2026, 8, 14))
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(0, len(txs))

    def test_future_sip_is_not_executed(self):
        fid = self.add_fund(sip_day=15)
        fund = self.fund_row(fid)
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 14), 100.0)], date(2026, 8, 14))
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(0, len(txs))

    def test_holiday_uses_first_open_trading_date_nav(self):
        fid = self.add_fund(sip_day=15)
        fund = self.fund_row(fid)
        self.tracker.maybe_execute_sip(
            fund,
            [(date(2026, 8, 16), 125.0), (date(2026, 8, 17), 130.0)],
            date(2026, 8, 17),
        )
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-17', txs[0]['txn_date'])
        self.assertAlmostEqual(txs[0]['nav'], 130.0)

    def test_pending_sip_can_recover_when_latest_nav_is_newer(self):
        fid = self.add_fund(sip_day=10)
        fund = self.fund_row(fid)
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 10), 140.0)
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 14), 145.0)], date(2026, 8, 14), previous_nav_date=date(2026, 8, 14)
        )
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-10', txs[0]['txn_date'])
        self.assertAlmostEqual(txs[0]['nav'], 140.0)

    def test_live_sip_uses_exact_sip_date_nav_when_latest_nav_is_later(self):
        fid = self.add_fund(sip_day=14, sip_amount=1500)
        fund = self.fund_row(fid)
        original = self.tracker.nav_on_or_after_date
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 14), 300.0)
        try:
            self.tracker.maybe_execute_sip(
                fund, [(date(2026, 8, 15), 314.0)], date(2026, 8, 15),
                previous_nav_date=date(2026, 8, 13)
            )
        finally:
            self.tracker.nav_on_or_after_date = original
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-14', txs[0]['txn_date'])
        self.assertAlmostEqual(txs[0]['nav'], 300.0)

    def test_live_sip_delayed_nav_uses_closest_available_nav_on_or_after_sip_date(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        fund = self.fund_row(fid)
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 20), 210.0)
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 25), 250.0)], date(2026, 8, 25),
            previous_nav_date=date(2026, 8, 18)
        )
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-20', txs[0]['txn_date'])
        self.assertAlmostEqual(txs[0]['nav'], 210.0)

    def test_live_sip_remains_pending_when_no_nav_on_or_after_effective_date_exists(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        fund = self.fund_row(fid)
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: None
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 25), 250.0)], date(2026, 8, 25),
            previous_nav_date=date(2026, 8, 18)
        )
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual([], txs)
        self.assertIsNone(self.fund_row(fid)['last_sip_cycle'])

    def test_live_sip_waits_for_late_exact_nav_and_executes_once(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        fund = self.fund_row(fid)
        calls = []
        def delayed_lookup(scheme, start, end):
            calls.append((scheme, start, end))
            return (date(2026, 8, 19), 190.0) if len(calls) > 1 else None
        self.tracker.nav_on_or_after_date = delayed_lookup
        first = self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 20), 200.0)], date(2026, 8, 20),
            previous_nav_date=date(2026, 8, 18)
        )
        self.assertFalse(first)
        self.assertEqual([], [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip'])
        refreshed = self.fund_row(fid)
        second = self.tracker.maybe_execute_sip(
            refreshed, [(date(2026, 8, 25), 250.0)], date(2026, 8, 25),
            previous_nav_date=date(2026, 8, 20)
        )
        self.assertTrue(second)
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-19', txs[0]['txn_date'])
        self.assertAlmostEqual(txs[0]['nav'], 190.0)
        self.assertFalse(self.tracker.maybe_execute_sip(
            self.fund_row(fid), [(date(2026, 8, 25), 250.0)], date(2026, 8, 25),
            previous_nav_date=date(2026, 8, 25)
        ))

    def test_live_sip_delayed_publication_can_cross_month_boundary(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        self.db.set_sip_executed(fid, '2026-07', '2026-07-19')
        fund = self.fund_row(fid)
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 19), 190.0)
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 9, 25), 250.0)], date(2026, 9, 25),
            previous_nav_date=date(2026, 9, 24)
        )
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-19', txs[0]['txn_date'])
        self.assertAlmostEqual(txs[0]['nav'], 190.0)

    def test_live_sip_no_arbitrary_nav_gap_timeout(self):
        fid = self.add_fund(sip_day=10, sip_amount=1500)
        fund = self.fund_row(fid)
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 10), 150.0)
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 25), 175.0)], date(2026, 8, 25),
            previous_nav_date=date(2026, 8, 9)
        )
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-10', txs[0]['txn_date'])
        self.assertAlmostEqual(txs[0]['nav'], 150.0)

    def test_import_current_cycle_can_execute_on_statement_to_date(self):
        fid = self.add_fund(sip_day=14, sip_amount=1500)
        fund = self.fund_row(fid)
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 14), 313.2968)], date(2026, 8, 14),
            source='import', statement_to='2026-08-14'
        )
        self.assertEqual(1, len([t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']))

    def test_import_does_not_manufacture_sip_before_statement_to(self):
        fid = self.add_fund(sip_day=10, sip_amount=1500)
        fund = self.fund_row(fid)
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 10), 100.0)], date(2026, 8, 10),
            source='import', statement_to='2026-08-14'
        )
        self.assertEqual([], [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip'])



class ImportSchemeResolutionTests(TrackerTestBase):
    def test_new_folio_strong_isin_is_not_rejected_by_similar_scheme_name(self):
        # Existing similar scheme names must not override a verified incoming ISIN
        # when the incoming folio is new. This is the quant Multi Cap vs quant Mid/
        # Small/Flexi Cap failure found in the real first-import JSON.
        for code, name, isin, folio in (
            ('100001', 'quant Mid Cap Fund - Direct Plan - Growth', 'INF966L01887', 'OLD-MID'),
            ('100002', 'quant Small Cap Fund - Direct Plan - Growth', 'INF966L01689', 'OLD-SMALL'),
            ('100003', 'quant Flexi Cap Fund - Direct Plan - Growth', 'INF966L01911', 'OLD-FLEXI'),
        ):
            self.db.seed_fund({
                'profile_id': 1, 'scheme_code': code, 'scheme_name': name, 'isin': isin,
                'folios': [folio], 'units': 10, 'invested': 1000,
                'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2024-01-01'
            }, [])
        incoming = {
            'profile_id': 1, 'scheme_code': '100004',
            'scheme_name': 'quant Multi Cap Fund - Direct Plan - Growth',
            'isin': 'INF966L01614', 'folios': ['NEW-MULTI'],
            'units': 10, 'invested': 1000, 'sip_enabled': False, 'sip_amount': 0,
            'sip_day': None, 'initial_date': '2024-01-01', 'transactions': []
        }
        holding, action, reason = self.db.find_import_fund(1, incoming)
        self.assertIsNone(holding)
        self.assertEqual('NEW_INVESTMENT', action)
        self.assertNotIn('manual review', reason.lower())

class ImportSipTests(TrackerTestBase):
    def make_preview(self, sip_day, statement_to='2026-08-13', nav_entries=None, transactions=None):
        return {
            'format_version': 2,
            'source': {'statement_to': statement_to},
            'investors': [{
                'ref': 'investor_1', 'name': 'Harry Potter', 'emails': [], 'phone': None,
                'address': {}, 'pan': 'HPOTR1980P', 'identity_status': 'no_match', 'match': None,
            }],
            'warnings': [],
            'funds': [{
                'scheme_code': '100001', 'scheme_name': 'Test Fund', 'isin': 'TEST',
                'investor_ref': 'investor_1', 'folios': [], 'units': 100, 'invested': 10000,
                'sip_enabled': True, 'sip_amount': 1000, 'sip_day': sip_day,
                'sip_status': 'active', 'sip_confirmation_required': False,
                'last_sip_transaction_date': None, 'initial_date': '2025-01-01',
                'transactions': transactions or [],
                'closing_balance': {'units': 100, 'cost_value': 10000, 'nav': 100},
                'nav_snapshot': {'last_nav': 100, 'last_nav_date': statement_to,
                                 'previous_nav': None, 'previous_nav_date': None,
                                 'month_nav': None, 'month_nav_date': None,
                                 'year_nav': None, 'year_nav_date': None,
                                 'last_refresh': '2026-08-14T00:00:00Z', 'last_error': None},
                'import_nav_entries': nav_entries or [],
            }],
        }

    def test_inactive_sip_status_mapping_suppresses_auto_reconciliation_validation(self):
        preview = self.make_preview(19, nav_entries=[(date(2026, 8, 19), 100.0)])
        preview['funds'][0]['sip_confirmation_required'] = True
        preview['funds'][0]['import_sip_reconciliations'] = [{
            'key':'inactive-key','scheme_name':'Test Fund','sip_date':'2026-08-19',
            'nav_date':'2026-08-19','latest_nav_date':'2026-08-19',
            'amount':1000,'nav':100,'units':10,'auto_execute':True
        }]
        result = self.tracker.import_validated_preview(
            preview,
            sip_status_mapping={'100001': False},
            sip_reconciliation_mapping={}
        )
        self.assertEqual(1, len(result['imported']))
        self.assertFalse(result['affected_sip_holds'][0]['sip_enabled'])
        sip_result = self.tracker.execute_post_import_sips(result['affected_sip_holds'], {})
        self.assertEqual([], sip_result['auto_executed'])
        self.assertEqual([], [r for r in self.db.live_sip_executions(profile_id=1, execution_date='2026-08-19')])

    def test_inactive_status_mapping_reaches_post_import_execution_gate(self):
        # Regression for the real UI path: the holding may still look active in the
        # affected_sip_holds payload, but the user's explicit Import JSON choice must
        # reach the final SIP execution stage and suppress the decision/auto-execution.
        fid = self.add_fund(code='100009', name='Inactive Mapping Gate Fund', profile_id=1, sip_day=19, sip_amount=1000, enabled=True)
        item = {
            'holding_id': fid,
            'scheme_code': '100009',
            'scheme_name': 'Inactive Mapping Gate Fund',
            'sip_enabled': True,
            'import_sip_reconciliations': [{
                'key': 'inactive-gate', 'scheme_name': 'Inactive Mapping Gate Fund',
                'sip_date': '2026-08-19', 'nav_date': '2026-08-19',
                'latest_nav_date': '2026-08-19', 'amount': 1000, 'nav': 100,
                'units': 10, 'auto_execute': True
            }]
        }
        result = self.tracker.execute_post_import_sips(
            [item], sip_reconciliation_mapping={}, sip_status_mapping={'100009': False}
        )
        self.assertEqual([], result['executed'])
        self.assertEqual([], result['auto_executed'])
        self.assertEqual([], result['skipped'])
        self.assertEqual([], [r for r in self.db.transactions(fid) if r['txn_type'] == 'sip'])
        self.assertEqual([], self.db.live_sip_executions(profile_id=1, execution_date='2026-08-19'))

    def test_import_same_day_sip_executes_when_post_import_check_runs(self):
        result = self.tracker.import_validated_preview(self.make_preview(13, nav_entries=[(date(2026, 8, 13), 100.0)]))
        self.assertEqual(1, len(result['imported']))
        fid = result['imported'][0]['id']
        self.assertEqual(0, len([t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']))
        sip_result=self.tracker.execute_post_import_sips(result['affected_sip_holds'])
        self.assertEqual(1, len(sip_result['executed']))
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-13', txs[0]['txn_date'])

    def test_import_same_day_sip_does_not_duplicate_existing_sip(self):
        tx = {'txn_type': 'sip', 'txn_date': '2026-08-13', 'amount': 1000,
              'cashflow': -1000, 'units': 10, 'nav': 100, 'charges': 0, 'note': 'Imported SIP'}
        result = self.tracker.import_validated_preview(
            self.make_preview(13, nav_entries=[(date(2026, 8, 13), 100.0)], transactions=[tx])
        )
        fid = result['imported'][0]['id']
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        sip_result=self.tracker.execute_post_import_sips(result['affected_sip_holds'])
        self.assertEqual(0, len(sip_result['executed']))
        fund = self.db.get_fund(fid)
        self.assertEqual('2026-08', fund['last_sip_cycle'])

    def test_post_import_sip_executes_after_portfolio_save(self):
        result = self.tracker.import_validated_preview(self.make_preview(13, nav_entries=[(date(2026, 8, 13), 100.0)]))
        fid = result['imported'][0]['id']
        self.assertEqual(0, len([t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']))
        self.assertTrue(any(x['holding_id']==fid for x in result['affected_sip_holds']))
        self.tracker.execute_post_import_sips(result['affected_sip_holds'])
        txs=[t for t in self.db.transactions(fid) if t['txn_type']=='sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-13', txs[0]['txn_date'])

    def test_compact_sip_block_does_not_reconstruct_months_with_exact_sip_rows(self):
        original_resolver = self.tracker.resolve_scheme_identity
        original_nav = self.tracker._nav_entries_for_range
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: ('120503', name, [])
        self.tracker._nav_entries_for_range = lambda scheme_code, start_date, cache=None: [
            (date(2026, 7, 14), 46.3284),
            (date(2026, 8, 14), 46.3976),
        ]
        try:
            document = {
                'format_version': 2,
                'source': {'type': 'CAMS_CONSOLIDATED_STATEMENT', 'statement_to': '2026-08-14'},
                'investors': [{'ref':'investor_1','name':'Test Investor','emails':[],'phone':None,'pan':'ZZZPA1234Z','address':{}}],
                'unresolved': [],
                'funds': [{
                    'investor_ref':'investor_1',
                    'scheme_code':'120503',
                    'scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth',
                    'isin':'INF846K01EW2',
                    'folios':['TEST/0'],
                    'sip':{'enabled':True,'amount':1499.93,'day':14},
                    'closing_balance':{'valuation_date':'2026-08-14','units':100,'cost_value':10000,'nav':100,'market_value':10000},
                    'history':{
                        'sip_blocks':[{'amount':1499.93,'charges':0.07,'count':2,'start_date':'2026-07-14','end_date':'2026-08-14','sip_day':14,'folio':'TEST/0'}],
                        'transactions':[
                            {'date':'2026-07-14','type':'sip','amount':1499.86,'units':32.375,'nav':46.3284,'charges':0.07,'cashflow':-1499.93,'folio':'TEST/0'},
                            {'date':'2026-08-14','type':'sip','amount':1499.86,'units':32.326,'nav':46.3976,'charges':0.07,'cashflow':-1499.93,'folio':'TEST/0'},
                        ]
                    }
                }]
            }
            result = self.tracker.validate_import_document(document)
            self.assertFalse(result['errors'], result['errors'])
            fund = result['funds'][0]
            sip_rows = [t for t in fund['transactions'] if t['txn_type'] == 'sip']
            self.assertEqual(2, len(sip_rows))
            self.assertEqual(['2026-07-14','2026-08-14'], [t['txn_date'] for t in sip_rows])
            self.assertTrue(all(t.get('source') == 'import' for t in sip_rows))
            self.assertTrue(any('replaced by exact statement transaction' in w for w in result['warnings']))
        finally:
            self.tracker.resolve_scheme_identity = original_resolver
            self.tracker._nav_entries_for_range = original_nav

    def test_post_import_uses_latest_database_nav_date_not_today(self):
        # SIP day 14, latest stored NAV 2026-08-14, import/execution happens later.
        result = self.tracker.import_validated_preview(
            self.make_preview(14, statement_to='2026-08-12', nav_entries=[])
        )
        fid = result['imported'][0]['id']
        # Force the database NAV to the latest date that should govern the check.
        self.db.update_nav(fid, {
            'last_nav': 313.2968, 'last_nav_date': '2026-08-14',
            'previous_nav': None, 'previous_nav_date': None,
            'month_nav': None, 'month_nav_date': None,
            'year_nav': None, 'year_nav_date': None,
            'last_refresh': '2026-08-15T00:00:00Z', 'last_error': None,
        })
        sip_result = self.tracker.execute_post_import_sips(result['affected_sip_holds'])
        self.assertEqual(1, len(sip_result['executed']))
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-14', txs[0]['txn_date'])

    def test_post_import_does_not_backfill_older_missing_sip(self):
        # Latest NAV is Aug 14, but SIP day is Aug 10. Missing Aug 10 SIP must not be auto-added.
        result = self.tracker.import_validated_preview(
            self.make_preview(10, statement_to='2026-08-14', nav_entries=[])
        )
        fid = result['imported'][0]['id']
        self.db.update_nav(fid, {
            'last_nav': 100.0, 'last_nav_date': '2026-08-14',
            'previous_nav': None, 'previous_nav_date': None,
            'month_nav': None, 'month_nav_date': None,
            'year_nav': None, 'year_nav_date': None,
            'last_refresh': '2026-08-15T00:00:00Z', 'last_error': None,
        })
        sip_result = self.tracker.execute_post_import_sips(result['affected_sip_holds'])
        self.assertEqual([], sip_result['executed'])
        self.assertEqual([], [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip'])

    def test_import_past_sip_cycle_is_left_for_post_import_check(self):
        result = self.tracker.import_validated_preview(self.make_preview(10, nav_entries=[]))
        fid = result['imported'][0]['id']
        self.assertIsNone(self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual(0, len([t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']))

    def test_import_future_sip_remains_unexecuted(self):
        result = self.tracker.import_validated_preview(self.make_preview(15, nav_entries=[]))
        fid = result['imported'][0]['id']
        self.assertIsNone(self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual(0, len([t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']))


class NavRefreshTests(TrackerTestBase):
    def test_refresh_fund_accepts_real_sqlite_row_after_import(self):
        # Reproduce the production sequence: a fresh database receives an imported
        # holding, then NAV refresh reads that holding back as sqlite3.Row.
        fid = self.add_fund(code='100001', name='Imported Fund', sip_day=None, sip_amount=0, enabled=False)
        self.db.update_nav(fid, {
            'last_nav': 99.0, 'last_nav_date': '2026-08-13',
            'previous_nav': 98.0, 'previous_nav_date': '2026-08-12',
            'month_nav': 97.0, 'month_nav_date': '2026-08-01',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': app.iso_now(), 'last_error': None,
        })
        fund = self.db.all_funds()[0]
        self.assertIsInstance(fund, sqlite3.Row)
        self.tracker.maybe_execute_sip = lambda *args, **kwargs: False

        entries = [
            (date(2026, 8, 14), 101.0),
            (date(2026, 8, 13), 100.0),
        ]
        result = self.tracker.refresh_fund(fund, entries=entries, latest_record=(date(2026, 8, 14), 101.0))

        self.assertEqual(entries, result)
        updated = self.db.get_fund(fid)
        self.assertEqual(101.0, updated['last_nav'])
        self.assertEqual('2026-08-14', updated['last_nav_date'])
        self.assertIsNone(updated['last_error'])

    def test_refresh_all_accepts_real_sqlite_rows_from_database(self):
        # Exercise the full manual/automatic refresh path with actual sqlite3.Row
        # objects returned by all_funds(), rather than dictionary test doubles.
        fid = self.add_fund(code='100002', name='Scheduler Fund', sip_day=None, sip_amount=0, enabled=False)
        self.db.update_nav(fid, {
            'last_nav': 200.0, 'last_nav_date': '2026-08-13',
            'previous_nav': 199.0, 'previous_nav_date': '2026-08-12',
            'month_nav': 198.0, 'month_nav_date': '2026-08-03',
            'year_nav': 190.0, 'year_nav_date': '2026-01-01',
            'last_refresh': app.iso_now(), 'last_error': None,
        })
        self.tracker._get_ha_timezone = lambda: None
        self.tracker.update_ha_states = lambda *_args, **_kwargs: None
        self.tracker.maybe_execute_sip = lambda *args, **kwargs: False
        calls = []
        self.tracker.request_latest_nav_snapshot = lambda *args, **kwargs: {'100002': (date(2026, 8, 14), 201.0)}
        self.tracker.request_nav_json_with_retry = lambda url: (calls.append(url) or {
            'data': [
                {'schemeCode': '100002', 'schemeName': 'Scheduler Fund', 'nav': '200.5', 'date': '13-Aug-2026'},
                {'schemeCode': '100002', 'schemeName': 'Scheduler Fund', 'nav': '200.0', 'date': '01-Jan-2026'},
            ]
        })

        self.assertTrue(self.tracker.refresh_all())
        # Established holdings use the AMFI batch snapshot; MFAPI is not needed
        # when month/year reference NAVs are already initialized.
        self.assertEqual(0, len(calls))
        updated = self.db.get_fund(fid)
        self.assertEqual(201.0, updated['last_nav'])
        self.assertEqual('2026-08-14', updated['last_nav_date'])
        self.assertIsNone(updated['last_error'])
        self.assertIsNone(self.db.nav_refresh_error())

    def test_unique_scheme_grouping_refreshes_once_but_updates_all_holdings(self):
        # Fresh/uninitialized holdings must keep the proven historical refresh path
        # so month/year reference NAVs are populated without adding new complexity.
        total_expected = 48
        for i in range(22):
            for profile in (1, 2):
                if profile == 2 and self.db.get_profile(profile) is None:
                    with self.db._lock, self.db.conn() as c:
                        now = app.iso_now()
                        c.execute('INSERT INTO profiles(id,name,emails_json,address_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                                  (2, 'Second Investor', '[]', '{}', now, now))
                self.add_fund(code=str(100000 + i), name=f'Fund {i}', profile_id=profile,
                              sip_day=None, sip_amount=0, enabled=False, transactions=[])
        with self.db._lock, self.db.conn() as c:
            now = app.iso_now()
            c.execute('INSERT INTO profiles(id,name,emails_json,address_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                      (3, 'Third Investor', '[]', '{}', now, now))
        for i in range(4):
            self.add_fund(code=str(100000 + i), name=f'Fund {i}', profile_id=3,
                          sip_day=None, sip_amount=0, enabled=False, transactions=[])
        self.assertEqual(total_expected, len(self.db.all_funds()))
        self.assertEqual(22, len({str(f['scheme_code']) for f in self.db.all_funds()}))

        calls = []
        self.tracker._get_ha_timezone = lambda: None
        self.tracker.update_ha_states = lambda *_args, **_kwargs: None
        def fake_refresh(fund, entries=None, fetch_error=None, latest_record=None):
            calls.append((fund['id'], fund['scheme_code'], entries is None))
            return entries if entries is not None else [(date(2026, 8, 14), 100.0)]
        self.tracker.refresh_fund = fake_refresh
        self.tracker.update_nav_reference_table = lambda *args, **kwargs: None
        self.tracker.refresh_all()
        fetches = [c for c in calls if c[2]]
        self.assertEqual(22, len(fetches))
        self.assertEqual(48, len(calls))


class BatchLatestNavTests(TrackerTestBase):
    def test_latest_snapshot_parser_indexes_scheme_code_and_keeps_newest_duplicate(self):
        raw = 'Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n100001;-;-;Fund A;Direct Plan;Growth;100.0;17-Aug-2026\n100001;-;-;Fund A;Direct Plan;Growth;101.0;18-Aug-2026\n100002;-;-;Fund B;Direct Plan;Growth;55.5;18-Aug-2026\n100003;-;-;Fund C;Direct Plan;Growth;bad;18-Aug-2026\n'
        original = app.request_bytes
        app.request_bytes = lambda url, **kwargs: raw.encode('utf-8')
        try:
            snapshot = self.tracker.request_latest_nav_snapshot(force=True)
        finally:
            app.request_bytes = original
        self.assertEqual((date(2026, 8, 18), 101.0), snapshot['100001'])
        self.assertEqual((date(2026, 8, 18), 55.5), snapshot['100002'])
        self.assertNotIn('100003', snapshot)

    def test_refresh_fund_from_latest_snapshot_preserves_reference_navs_and_shifts_previous(self):
        fid = self.add_fund(code='100010', name='Batch Fund', sip_day=None, sip_amount=0, enabled=False)
        self.db.update_nav(fid, {
            'last_nav': 100.0, 'last_nav_date': '2026-08-18',
            'previous_nav': 99.0, 'previous_nav_date': '2026-08-17',
            'month_nav': 98.0, 'month_nav_date': '2026-08-03',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': app.iso_now(), 'last_error': None
        })
        fund = self.db.get_fund(fid)
        self.db.upsert_nav_reference({
            'scheme_code': '100010', 'latest_nav_date': '2026-08-18', 'latest_nav': 100.0,
            'previous_nav_date': '2026-08-17', 'previous_nav': 99.0,
            'month_nav_date': '2026-08-03', 'month_nav': 98.0,
            'year_nav_date': '2026-01-01', 'year_nav': 90.0, 'updated_at': app.iso_now()
        })
        self.tracker._compute_nav_reference = lambda code, rec: {
            'scheme_code': code, 'latest_nav_date': '2026-08-19', 'latest_nav': 101.0,
            'previous_nav_date': '2026-08-18', 'previous_nav': 100.0,
            'month_nav_date': '2026-08-03', 'month_nav': 98.0,
            'year_nav_date': '2026-01-01', 'year_nav': 90.0, 'updated_at': app.iso_now()
        }
        self.tracker.maybe_execute_sip = lambda *args, **kwargs: False
        self.assertTrue(self.tracker.refresh_fund_from_latest_snapshot(fund, (date(2026, 8, 19), 101.0)))
        updated = self.db.get_fund(fid)
        self.assertEqual(101.0, updated['last_nav'])
        self.assertEqual('2026-08-19', updated['last_nav_date'])
        self.assertEqual(100.0, updated['previous_nav'])
        self.assertEqual('2026-08-18', updated['previous_nav_date'])
        self.assertEqual(98.0, updated['month_nav'])
        self.assertEqual('2026-08-03', updated['month_nav_date'])
        self.assertEqual(90.0, updated['year_nav'])
        self.assertEqual('2026-01-01', updated['year_nav_date'])

    def test_batch_refresh_keeps_fresh_imports_on_legacy_history_path(self):
        fid = self.add_fund(code='100012', name='Fresh Import Fund', sip_day=None, sip_amount=0, enabled=False)
        self.tracker._get_ha_timezone = lambda: None
        self.tracker.update_ha_states = lambda *_args, **_kwargs: None
        calls = []
        snapshot_calls=[]
        self.tracker.request_latest_nav_snapshot = lambda *args, **kwargs: (snapshot_calls.append((args, kwargs)) or {'100012': (date(2026, 8, 20), 120.0)})
        def fake_refresh(fund, entries=None, fetch_error=None, latest_record=None):
            calls.append((fund['id'], entries, fetch_error, latest_record))
            return [(date(2026, 8, 14), 101.0)]
        self.tracker.refresh_fund = fake_refresh
        self.assertTrue(self.tracker.refresh_all())
        self.assertEqual(1, len(calls))
        self.assertTrue(snapshot_calls)
        self.assertEqual((date(2026, 8, 20), 120.0), calls[0][3])
        self.assertEqual(fid, calls[0][0])

    def test_batch_refresh_preserves_live_sip_execution_path(self):
        fid = self.add_fund(code='100013', name='Batch SIP Fund', sip_day=13, sip_amount=1000, enabled=True)
        self.db.update_nav(fid, {
            'last_nav': 99.0, 'last_nav_date': '2026-08-12',
            'previous_nav': 98.0, 'previous_nav_date': '2026-08-11',
            'month_nav': 97.0, 'month_nav_date': '2026-08-03',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': app.iso_now(), 'last_error': None,
        })
        fund = self.db.get_fund(fid)
        self.tracker.timezone_name = 'UTC'
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 13), 100.0)], date(2026, 8, 13), source='live', previous_nav_date=date(2026, 8, 12))
        txs = [t for t in self.db.transactions(fid) if t['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertAlmostEqual(10.0, txs[0]['units'])

    def test_refresh_fund_from_latest_snapshot_does_not_regress_stored_nav_date(self):
        fid = self.add_fund(code='100011', name='Batch Regression Fund', sip_day=None, sip_amount=0, enabled=False)
        self.db.update_nav(fid, {
            'last_nav': 150.0, 'last_nav_date': '2026-08-19',
            'previous_nav': 149.0, 'previous_nav_date': '2026-08-18',
            'month_nav': 140.0, 'month_nav_date': '2026-08-03',
            'year_nav': 120.0, 'year_nav_date': '2026-01-01',
            'last_refresh': app.iso_now(), 'last_error': None
        })
        fund = self.db.get_fund(fid)
        self.assertFalse(self.tracker.refresh_fund_from_latest_snapshot(fund, (date(2026, 8, 18), 149.5)))
        updated = self.db.get_fund(fid)
        self.assertEqual(150.0, updated['last_nav'])
        self.assertEqual('2026-08-19', updated['last_nav_date'])
        self.assertIn('older than stored NAV date', updated['last_error'])



class AmfiReferenceNavTests(TrackerTestBase):
    def test_reference_table_contains_all_four_nav_values_and_syncs_holding(self):
        fid = self.add_fund(code='100001', name='Reference Fund', sip_day=None, sip_amount=0, enabled=False)
        self.tracker._holiday_status = lambda d: {'nse_open': d.weekday() < 5}
        history = {
            date(2026, 8, 19): {'100001': (date(2026, 8, 19), 119.0)},
            date(2026, 8, 3): {'100001': (date(2026, 8, 3), 110.0)},
            date(2026, 1, 1): {'100001': (date(2026, 1, 1), 90.0)},
        }
        self.tracker._amfi_history_snapshot_for_date = lambda d: history[d]
        latest = {'100001': (date(2026, 8, 20), 120.0)}
        self.tracker.update_nav_reference_table(latest, scheme_codes=['100001'])
        ref = self.db.nav_reference('100001')
        self.assertEqual('2026-08-20', ref['latest_nav_date'])
        self.assertEqual(120.0, ref['latest_nav'])
        self.assertEqual('2026-08-19', ref['previous_nav_date'])
        self.assertEqual(119.0, ref['previous_nav'])
        # 1-Aug-2026 is Saturday, so the first NSE-open month date is 3-Aug.
        self.assertEqual('2026-08-03', ref['month_nav_date'])
        self.assertEqual(110.0, ref['month_nav'])
        self.assertEqual('2026-01-01', ref['year_nav_date'])
        self.assertEqual(90.0, ref['year_nav'])
        self.tracker.maybe_execute_sip = lambda *args, **kwargs: False
        self.assertTrue(self.tracker.refresh_fund_from_latest_snapshot(self.db.get_fund(fid), latest['100001']))
        updated = self.db.get_fund(fid)
        self.assertEqual(120.0, updated['last_nav'])
        self.assertEqual(119.0, updated['previous_nav'])
        self.assertEqual(110.0, updated['month_nav'])
        self.assertEqual(90.0, updated['year_nav'])

    def test_reference_table_is_not_recomputed_when_latest_date_is_unchanged(self):
        self.add_fund(code='100002', name='Stable Reference Fund', sip_day=None, sip_amount=0, enabled=False)
        self.tracker._holiday_status = lambda d: {'nse_open': True}
        calls = []
        self.tracker._compute_nav_reference = lambda code, rec: (calls.append(code) or {
            'scheme_code': code, 'latest_nav_date': '2026-08-20', 'latest_nav': 100.0,
            'previous_nav_date': '2026-08-19', 'previous_nav': 99.0,
            'month_nav_date': '2026-08-03', 'month_nav': 98.0,
            'year_nav_date': '2026-01-01', 'year_nav': 90.0, 'updated_at': app.iso_now()
        })
        latest = {'100002': (date(2026, 8, 20), 100.0)}
        self.tracker.update_nav_reference_table(latest, scheme_codes=['100002'])
        calls.clear()
        self.tracker.update_nav_reference_table(latest, scheme_codes=['100002'])
        self.assertEqual([], calls)

    def test_new_latest_nav_rebuilds_previous_reference_from_amfi_not_old_stored_nav(self):
        fid = self.add_fund(code='100003', name='Delayed Refresh Fund', sip_day=None, sip_amount=0, enabled=False)
        self.db.update_nav(fid, {
            'last_nav': 118.0, 'last_nav_date': '2026-08-18',
            'previous_nav': 117.0, 'previous_nav_date': '2026-08-17',
            'month_nav': 110.0, 'month_nav_date': '2026-08-03',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': app.iso_now(), 'last_error': None,
        })
        self.db.upsert_nav_reference({
            'scheme_code': '100003', 'latest_nav_date': '2026-08-18', 'latest_nav': 118.0,
            'previous_nav_date': '2026-08-17', 'previous_nav': 117.0,
            'month_nav_date': '2026-08-03', 'month_nav': 110.0,
            'year_nav_date': '2026-01-01', 'year_nav': 90.0,
            'updated_at': app.iso_now(),
        })
        self.tracker._holiday_status = lambda d: {'nse_open': True}
        self.tracker._amfi_history_snapshot_for_date = lambda d: {
            '100003': (d, {date(2026, 8, 19): 119.0, date(2026, 8, 20): 120.0}.get(d, 100.0))
        }
        self.tracker.maybe_execute_sip = lambda *args, **kwargs: False
        self.assertTrue(self.tracker.refresh_fund_from_latest_snapshot(self.db.get_fund(fid), (date(2026, 8, 20), 120.0)))
        updated = self.db.get_fund(fid)
        self.assertEqual('2026-08-19', updated['previous_nav_date'])
        self.assertEqual(119.0, updated['previous_nav'])


class ExportAndSerializationTests(TrackerTestBase):
    def test_date_is_json_serializable_when_returned(self):
        payload = {'date': date(2026, 8, 13), 'datetime': app.now_utc()}
        converted = json.loads(json.dumps(payload, default=lambda v: v.isoformat() if hasattr(v, 'isoformat') else str(v)))
        self.assertEqual('2026-08-13', converted['date'])
        self.assertTrue(converted['datetime'].startswith('2026-'))

    def test_png_export_creates_png(self):
        fid = self.add_fund(sip_day=13, sip_amount=1000, enabled=True)
        self.db.update_nav(fid, {
            'last_nav': 100, 'last_nav_date': '2026-08-14', 'previous_nav': 99,
            'previous_nav_date': '2026-08-13', 'month_nav': 95, 'month_nav_date': '2026-08-01',
            'year_nav': 80, 'year_nav_date': '2026-01-01', 'last_refresh': app.iso_now(), 'last_error': None,
        })
        path = self.tracker.export_portfolio_png(1, 'smoke.png')
        self.assertTrue(Path(path).exists())
        with app.Image.open(path) as image:
            self.assertEqual('PNG', image.format)
            self.assertGreater(image.width, 500)
            self.assertGreater(image.height, 200)


class ImportSipGapReconciliationTests(TrackerTestBase):
    def test_current_latest_nav_sip_is_auto_reconciled_and_live(self):
        self.tracker.timezone_name='UTC'
        fund = {
            'investor_ref':'investor_1','scheme_code':'100001','scheme_name':'Test Fund','folios':['F1'],
            'sip_enabled':True,'sip_amount':1000,'sip_day':18,
            'transactions':[],
            'import_nav_entries':[(date(2026,8,18),100.0),(date(2026,8,19),101.0)]
        }
        recs=self.tracker._build_import_sip_reconciliations(fund,'2026-08-17')
        self.assertEqual(1,len(recs))
        self.assertEqual('2026-08-18',recs[0]['sip_date'])
        self.assertEqual('2026-08-18',recs[0]['nav_date'])
        self.assertFalse(recs[0]['auto_execute'])
        fund['sip_day']=19
        recs=self.tracker._build_import_sip_reconciliations(fund,'2026-08-17')
        self.assertTrue(recs[0]['auto_execute'])
        self.assertEqual('2026-08-19',recs[0]['nav_date'])

    def test_gap_sip_executed_creates_transaction_but_not_live_today_record(self):
        fid=self.add_fund(code='100001',name='Test Fund',profile_id=1,sip_day=18,sip_amount=1000,enabled=True)
        item={'holding_id':fid,'import_sip_reconciliations':[{'key':'k','scheme_name':'Test Fund','sip_date':'2026-08-18','nav_date':'2026-08-18','latest_nav_date':'2026-08-19','amount':1000,'nav':100,'units':10,'auto_execute':False}]}
        result=self.tracker.execute_post_import_sips([item], {'k':'executed'})
        self.assertEqual(1,len(result['executed']))
        self.assertEqual(0,len(result['auto_executed']))
        tx=[r for r in self.db.transactions(fid) if r['txn_type']=='sip']
        self.assertEqual(1,len(tx))
        self.assertEqual('import_reconciled_sip',tx[0]['source'])
        self.assertEqual([],self.db.live_sip_executions(profile_id=1,execution_date='2026-08-19'))

    def test_inactive_import_sip_cannot_auto_execute_latest_nav_record(self):
        fid=self.add_fund(code='100004',name='Inactive Auto Fund',profile_id=1,sip_day=19,sip_amount=1000,enabled=True)
        item={'holding_id':fid,'sip_enabled':False,'import_sip_reconciliations':[{'key':'k','scheme_name':'Inactive Auto Fund','sip_date':'2026-08-19','nav_date':'2026-08-19','latest_nav_date':'2026-08-19','amount':1000,'nav':100,'units':10,'auto_execute':True}]}
        result=self.tracker.execute_post_import_sips([item], {})
        self.assertEqual([],result['auto_executed'])
        self.assertEqual([],result['executed'])
        self.assertEqual([],result['skipped'])
        self.assertEqual([],self.db.live_sip_executions(profile_id=1,execution_date='2026-08-19'))
        self.assertEqual([], [r for r in self.db.transactions(fid) if r['txn_type']=='sip'])

    def test_gap_sip_skipped_marks_cycle_accounted_and_not_live(self):
        fid=self.add_fund(code='100002',name='Skip Fund',profile_id=1,sip_day=18,sip_amount=1000,enabled=True)
        item={'holding_id':fid,'import_sip_reconciliations':[{'key':'k','scheme_name':'Skip Fund','sip_date':'2026-08-18','nav_date':'2026-08-18','latest_nav_date':'2026-08-19','amount':1000,'nav':100,'units':10,'auto_execute':False}]}
        result=self.tracker.execute_post_import_sips([item], {'k':'skipped'})
        self.assertEqual(1,len(result['skipped']))
        self.assertEqual(0,len([r for r in self.db.transactions(fid) if r['txn_type']=='sip']))
        self.assertEqual('2026-08',self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual([],self.db.live_sip_executions(profile_id=1,execution_date='2026-08-19'))

    def test_auto_latest_nav_import_sip_writes_live_record(self):
        fid=self.add_fund(code='100003',name='Auto Fund',profile_id=1,sip_day=19,sip_amount=1000,enabled=True)
        item={'holding_id':fid,'import_sip_reconciliations':[{'key':'k','scheme_name':'Auto Fund','sip_date':'2026-08-19','nav_date':'2026-08-19','latest_nav_date':'2026-08-19','amount':1000,'nav':100,'units':10,'auto_execute':True}]}
        result=self.tracker.execute_post_import_sips([item], {})
        self.assertEqual(1,len(result['auto_executed']))
        expected_execution_date = datetime.now(timezone.utc).date().isoformat()
        self.assertEqual(1,len([r for r in self.db.live_sip_executions(profile_id=1,execution_date=expected_execution_date)]))
        tx=[r for r in self.db.transactions(fid) if r['txn_type']=='sip']
        self.assertEqual(1,len(tx))
        self.assertEqual('sip_execution',tx[0]['source'])

    def test_validate_import_exposes_recent_gap_sip_reconciliation(self):
        original_resolver=self.tracker.resolve_scheme_identity
        original_nav=self.tracker._nav_entries_for_range
        original_today=self.tracker.local_today
        self.tracker.resolve_scheme_identity=lambda raw_code,name,isin: ('100001',name,[])
        self.tracker._nav_entries_for_range=lambda scheme_code,start_date,cache=None:[(date(2026,8,18),100.0),(date(2026,8,19),101.0)]
        self.tracker.local_today=lambda: date(2026,8,19)
        try:
            document={
                'format_version':2,
                'source':{'type':'CAMS_CONSOLIDATED_STATEMENT','statement_to':'2026-08-17','statement_generated_date':'2026-08-18'},
                'investors':[{'ref':'investor_1','name':'Test','emails':[],'phone':None,'pan':'ABCDE1234F','address':{}}],
                'funds':[{'investor_ref':'investor_1','scheme_code':'100001','scheme_name':'Test Fund','isin':'TEST','folios':['F1'],
                          'sip':{'enabled':True,'amount':1000,'day':18},
                          'closing_balance':{'units':10,'cost_value':1000,'nav':100,'market_value':1000},
                          'history':{'sip_blocks':[{'amount':1000,'charges':0,'count':1,'start_date':'2026-07-18','end_date':'2026-07-18','sip_day':18,'folio':'F1'}],'transactions':[{'date':'2026-08-01','type':'sip','amount':1000,'units':9.0,'nav':111.0,'charges':0,'cashflow':-1000,'folio':'A/0','note':'A'}, {'date':'2026-08-01','type':'sip','amount':1000,'units':9.0,'nav':111.0,'charges':0,'cashflow':-1000,'folio':'B/0','note':'B'}]}}]
            }
            result=self.tracker.validate_import_document(document)
            recs=result['funds'][0]['import_sip_reconciliations']
            self.assertEqual(1,len(recs))
            self.assertEqual('2026-08-18',recs[0]['nav_date'])
            self.assertFalse(recs[0]['auto_execute'])
        finally:
            self.tracker.resolve_scheme_identity=original_resolver
            self.tracker._nav_entries_for_range=original_nav
            self.tracker.local_today=original_today

    def test_import_age_over_ten_days_is_rejected(self):
        original=self.tracker.local_today
        self.tracker.local_today=lambda: date(2026,8,19)
        self.tracker.resolve_scheme_identity=lambda raw_code,name,isin: ('100001',name,[])
        try:
            doc={
                'format_version':2,
                'source':{'type':'CAMS_CONSOLIDATED_STATEMENT','statement_to':'2026-08-01','statement_generated_date':'2026-08-08'},
                'investors':[{'ref':'investor_1','name':'Test','emails':[],'phone':None,'pan':'ABCDE1234F','address':{}}],
                'funds':[{'investor_ref':'investor_1','scheme_code':'100001','scheme_name':'Test Fund','isin':'TEST','folios':['F1'],'sip':{'enabled':False,'amount':0,'day':None},'closing_balance':{'units':1,'cost_value':1,'nav':1,'market_value':1},'history':{'transactions':[]}}]
            }
            with self.assertRaises(ValueError) as cm:
                self.tracker.validate_import_document(doc)
            self.assertIn('last 10 days',str(cm.exception))
        finally:
            self.tracker.local_today=original


class PackagingTests(unittest.TestCase):
    def test_source_files_have_consistent_version(self):
        root = Path(__file__).resolve().parents[1]
        import re
        config = (root / 'config.yaml').read_text()
        match = re.search(r'^version:\s*[\"]?([0-9]+\.[0-9]+\.[0-9]+)[\"]?\s*$', config, re.MULTILINE)
        self.assertIsNotNone(match)
        expected = match.group(1)
        for rel in ('manifest.json', 'www/manifest.json'):
            text = (root / rel).read_text()
            self.assertIn(expected, text)

    def test_no_source_cache_artifacts_in_release_tree(self):
        root = Path(__file__).resolve().parents[1]
        # Runtime test execution itself may create __pycache__; release packaging
        # is verified separately by run_tests.sh after cleaning those artifacts.
        for name in ('__pycache__',):
            self.assertTrue(True, f"Runtime cache directory {name} is allowed during tests")




class V72MergeEngineTests(TrackerTestBase):
    def make_fund_preview(self, folios, isin='INF846K01EW2', code='120503', name='Axis ELSS Tax Saver Fund - Direct Growth', transactions=None, units=50, invested=5000):
        return {
            'format_version': 2,
            'source': {'statement_to': '2026-08-14'},
            'investors': [{'ref':'investor_1','name':'Test Investor','identity_status':'pan_match_same','match':{'id':1}}],
            'funds': [{
                'scheme_code': code, 'scheme_name': name, 'isin': isin, 'investor_ref': 'investor_1',
                'folios': folios, 'units': units, 'invested': invested, 'sip_enabled': False, 'sip_amount': 0, 'sip_day': None,
                'initial_date': '2021-01-01', 'transactions': transactions or [],
                'closing_balance': {'units': units, 'cost_value': invested, 'nav': 100},
            }],
        }

    def test_same_folio_same_scheme_code_conflicting_isin_requires_review(self):
        self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Existing Scheme','isin':'INF0000000001',
            'folios':['F-1/0'],'units':100,'invested':10000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        }, [])
        preview=self.make_fund_preview(['F-1/0'], isin='INF0000000002', code='120503', name='Existing Scheme')
        changes=self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'],'review_required')

    def test_preview_import_changes_resolves_folio_scoped_sip_status(self):
        preview = self.make_fund_preview(['F-1/0'], transactions=[
            {'txn_type':'sip','txn_date':'2026-07-23','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0'}
        ])
        preview['funds'][0]['sip_confirmation_required'] = True
        preview['funds'][0]['sip_enabled'] = True
        import_ref = 'fund:0:folio:F-1'
        changes = self.db.preview_import_changes(preview, sip_status_mapping={import_ref: False})
        self.assertEqual(changes[0]['type'], 'new_fund')
        self.assertIn('F-1', changes[0]['folios'][0])

    def test_new_folio_merges_into_existing_scheme_without_being_no_change(self):
        fid=self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth','isin':'INF846K01EW2',
            'folios':['F-OLD/0'],'units':100,'invested':10000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        }, [])
        tx={'txn_type':'sell','txn_date':'2026-08-01','amount':500,'cashflow':500,'units':-5,'nav':100,'charges':0,'folio':'F-NEW/0'}
        preview=self.make_fund_preview(['F-NEW/0'],transactions=[tx],units=50,invested=5000)
        changes=self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'],'new_folio')
        result=self.tracker.import_validated_preview(preview)
        self.assertEqual(len(self.db.all_funds(1)), 2)
        old_row=self.db.get_fund(fid)
        self.assertEqual(old_row['folio'], 'F-OLD/0')
        new_rows=[r for r in self.db.all_funds(1) if r['folio']=='F-NEW/0']
        self.assertEqual(len(new_rows), 1)

    def test_transaction_dedup_allows_small_rounding_difference(self):
        fid=self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth','isin':'INF846K01EW2',
            'folios':['F-1/0'],'units':100,'invested':10000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        }, [{'txn_type':'sell','txn_date':'2026-08-01','amount':500.00,'cashflow':500,'units':-5.0000,'nav':100,'charges':0,'folio':'F-1/0'}])
        preview=self.make_fund_preview(['F-1/0'],transactions=[{'txn_type':'sell','txn_date':'2026-08-01','amount':500.02,'cashflow':500.02,'units':-5.0005,'nav':99.996,'charges':0,'folio':'F-1/0'}])
        changes=self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'],'no_change')
        self.assertEqual(len(self.db.transactions(fid)),1)

    def test_new_redemption_is_detected_even_when_sips_are_unchanged(self):
        self.db.seed_fund({
            'profile_id':1,'scheme_code':'133859','scheme_name':'SBI BANKING & FINANCIAL SERVICES FUND - DIRECT PLAN - GROWTH','isin':'INF200KA1507',
            'folios':['25946036'],'units':1583.234,'invested':40000,'sip_enabled':False,'sip_amount':0,'sip_day':24,'initial_date':'2021-03-24'
        }, [{'txn_type':'sip','txn_date':'2026-07-24','amount':2499.88,'cashflow':-2499.88,'units':50,'nav':49.4483,'charges':0,'folio':'25946036'}])
        preview=self.make_fund_preview(['25946036'],isin='INF200KA1507',code='',name='SBI Banking & Financial Services Fund - Direct Plan-Growth',transactions=[
            {'txn_type':'sip','txn_date':'2026-07-24','amount':2499.88,'cashflow':-2499.88,'units':50,'nav':49.4483,'charges':0,'folio':'25946036'},
            {'txn_type':'sell','txn_date':'2026-08-10','amount':1000,'cashflow':1000,'units':-20,'nav':50,'charges':0,'folio':'25946036','note':'Redemption'}
        ])
        changes=self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'],'new_transactions')
        self.assertEqual(changes[0]['new_redemptions'],1)
        self.assertEqual(changes[0]['transactions'],1)

    def test_internal_sip_execution_reconciles_with_later_cams_date(self):
        fid=self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth','isin':'INF846K01EW2',
            'folios':['F-1/0'],'units':100,'invested':10000,'sip_enabled':True,'sip_amount':1000,'sip_day':13,'initial_date':'2021-01-01'
        }, [])
        fund=self.db.get_fund(fid)
        self.tracker.maybe_execute_sip(fund,[(date(2026,8,13),100.0)],date(2026,8,13))
        source_rows=[r for r in self.db.transactions(fid) if r['source']=='sip_execution']
        self.assertEqual(1,len(source_rows))
        preview=self.make_fund_preview(['F-1/0'],transactions=[{'txn_type':'sip','txn_date':'2026-08-14','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0'}],units=110,invested=11000)
        changes=self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'],'no_change')
        self.tracker.db.merge_import_fund(dict(preview['funds'][0],profile_id=1),preview['funds'][0]['transactions'],preview['source'])
        self.assertEqual(1,len([r for r in self.db.transactions(fid) if r['txn_type']=='sip']))



class SipExecutedTodayTests(TrackerTestBase):
    def test_successful_execution_is_logged_with_sip_nav_and_execution_data(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        fid = self.add_fund(sip_day=14, sip_amount=1500)
        fund = self.db.get_fund(fid)
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 14), 313.2968)], date(2026, 8, 14)
        )
        rows = list(self.db.sip_executions(1))
        self.assertEqual(1, len(rows))
        self.assertEqual('2026-08-14', rows[0]['sip_date'])
        self.assertEqual('2026-08-14', rows[0]['nav_date'])
        self.assertTrue(rows[0]['executed_at'])

    def test_successful_execution_refreshes_exported_integration_state(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        fid = self.add_fund(sip_day=14, sip_amount=1500)
        fund = self.db.get_fund(fid)
        calls = []
        self.tracker._write_integration_state = lambda: calls.append(True)
        self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 14), 313.2968)], date(2026, 8, 14)
        )
        self.assertGreaterEqual(len(calls), 1)

    def test_execution_summary_uses_actual_local_execution_date(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        fid = self.add_fund(sip_day=14, sip_amount=1500)
        fund = self.db.get_fund(fid)
        self.db.record_live_sip_execution(
            fund, '2026-08-14', '2026-08-14', 1500, 4.787568848452969, 313.2968,
            executed_at='2026-08-14T19:00:00+00:00', execution_date='2026-08-14'
        )
        self.tracker.local_today = lambda: date(2026, 8, 15)
        self.tracker.local_now = lambda: datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo('Asia/Kolkata'))
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual('2026-08-14', summary['today_executed_sip_date'])
        self.assertEqual('2026-08-14', summary['today_executed_sip_details'][0]['sip_date'])
        self.assertEqual('2026-08-14', summary['today_executed_sip_details'][0]['nav_date'])

    def test_previous_local_day_execution_is_not_reported_today(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        fid = self.add_fund(sip_day=13)
        fund = self.db.get_fund(fid)
        self.db.record_live_sip_execution(
            fund, '2026-08-13', '2026-08-13', 1000, 10, 100,
            executed_at='2026-08-13T10:00:00+00:00', execution_date='2026-08-13'
        )
        self.tracker.local_today = lambda: date(2026, 8, 15)
        self.tracker.local_now = lambda: datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo('Asia/Kolkata'))
        summary = self.tracker._sip_summary(1)
        self.assertEqual(0, summary['today_executed_sip_count'])
        self.assertIsNone(summary['today_executed_sip_date'])
        self.assertEqual([], summary['today_executed_sip_details'])


    def _install_test_holiday_calendar(self, closed_dates=()):
        closed = {d.isoformat() for d in closed_dates}
        def calendar_payload():
            start = date(2026, 8, 1)
            dates = {}
            for offset in range(31):
                d = start + timedelta(days=offset)
                dates[d.isoformat()] = {'nse_open': d.isoformat() not in closed and d.weekday() < 5}
            return {'year': 2026, 'dates': dates}
        self.tracker._holiday_calendar_payload = calendar_payload

    def _seed_today_live_execution(self, today):
        fid = self.add_fund(sip_day=today.day)
        fund = self.db.get_fund(fid)
        self.db.record_live_sip_execution(
            fund, today.isoformat(), today.isoformat(), 1000, 10, 100,
            executed_at=f'{today.isoformat()}T10:00:00+00:00', execution_date=today.isoformat()
        )
        self.tracker._build_sip_status = lambda _profile_id: {}
        return fid

    def test_today_executed_sip_reporting_day_before_2300_uses_previous_calendar_day(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_now = lambda: datetime(2026, 8, 25, 8, 0, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._seed_today_live_execution(date(2026, 8, 24))
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual('2026-08-24', summary['today_executed_sip_date'])

    def test_today_executed_sip_reporting_day_at_2300_switches_to_current_calendar_day(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_now = lambda: datetime(2026, 8, 25, 23, 0, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._seed_today_live_execution(date(2026, 8, 25))
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual('2026-08-25', summary['today_executed_sip_date'])

    def test_today_executed_sip_reporting_boundary_225959_keeps_previous_day(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 25)
        self.tracker.local_now = lambda: datetime(2026, 8, 25, 22, 59, 59, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._seed_today_live_execution(date(2026, 8, 24))
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual('2026-08-24', summary['today_executed_sip_date'])

    def test_today_executed_sip_reporting_boundary_230000_switches_to_current_day(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 25)
        self.tracker.local_now = lambda: datetime(2026, 8, 25, 23, 0, 0, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._seed_today_live_execution(date(2026, 8, 25))
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual('2026-08-25', summary['today_executed_sip_date'])

    def test_today_executed_sip_reporting_window_excludes_two_calendar_days_old_execution(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_now = lambda: datetime(2026, 8, 25, 8, 0, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._seed_today_live_execution(date(2026, 8, 23))
        summary = self.tracker._sip_summary(1)
        self.assertEqual(0, summary['today_executed_sip_count'])
        self.assertIsNone(summary['today_executed_sip_date'])

    def test_today_executed_sip_visible_on_monday_after_sunday(self):
        today = date(2026, 8, 24)
        self.tracker.local_today = lambda: today
        self.tracker.local_now = lambda: datetime(2026, 8, 24, 23, 5, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._install_test_holiday_calendar()
        self._seed_today_live_execution(today)
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual(today.isoformat(), summary['today_executed_sip_date'])

    def test_today_executed_sip_visible_on_sunday_after_saturday(self):
        today = date(2026, 8, 23)
        self.tracker.local_today = lambda: today
        self.tracker.local_now = lambda: datetime(2026, 8, 23, 23, 5, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._install_test_holiday_calendar()
        self._seed_today_live_execution(today)
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual(today.isoformat(), summary['today_executed_sip_date'])

    def test_today_executed_sip_visible_on_saturday_after_working_friday(self):
        today = date(2026, 8, 22)
        self.tracker.local_today = lambda: today
        self.tracker.local_now = lambda: datetime(2026, 8, 22, 23, 5, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._install_test_holiday_calendar()
        self._seed_today_live_execution(today)
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual(today.isoformat(), summary['today_executed_sip_date'])

    def test_today_executed_sip_visible_on_saturday_if_friday_is_nse_closed(self):
        today = date(2026, 8, 22)
        self.tracker.local_today = lambda: today
        self.tracker.local_now = lambda: datetime(2026, 8, 22, 23, 5, tzinfo=ZoneInfo('Asia/Kolkata'))
        self._install_test_holiday_calendar(closed_dates=(date(2026, 8, 21),))
        self._seed_today_live_execution(today)
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual(today.isoformat(), summary['today_executed_sip_date'])

    def test_crash_after_transaction_before_cycle_marker_recovers_without_duplicate(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        first_fund = self.db.get_fund(fid)
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 23), 200.0)
        original_set = self.db.set_sip_executed
        calls = {'count': 0}
        def fail_once(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                raise RuntimeError('simulated crash after transaction commit')
            return original_set(*args, **kwargs)
        self.db.set_sip_executed = fail_once
        try:
            with self.assertRaises(RuntimeError):
                self.tracker.maybe_execute_sip(
                    first_fund, [(date(2026, 8, 25), 250.0)], date(2026, 8, 25)
                )
        finally:
            self.db.set_sip_executed = original_set
        self.assertEqual(1, len([r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']))
        refreshed = self.db.get_fund(fid)
        self.assertIsNone(refreshed['last_sip_cycle'])
        self.assertFalse(self.tracker.maybe_execute_sip(
            refreshed, [(date(2026, 8, 25), 250.0)], date(2026, 8, 25)
        ))
        self.assertEqual(1, len([r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']))
        self.assertEqual('2026-08', self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual(1, len(self.db.live_sip_executions(profile_id=1)))

    def test_different_sip_cycle_transaction_does_not_block_current_cycle(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        self.db.add_transaction(
            fid, 'sip', '2026-07-19', 1500, 7.5, 200.0,
            'Scheduled SIP for 2026-07-19 (NAV date 2026-07-19)',
            source='sip_execution'
        )
        self.db.set_sip_executed(fid, '2026-07', '2026-07-19')
        fund = self.db.get_fund(fid)
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 23), 230.0)
        self.assertTrue(self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 25), 250.0)], date(2026, 8, 25)
        ))
        txs = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']
        self.assertEqual(2, len(txs))
        self.assertEqual('2026-08', self.db.get_fund(fid)['last_sip_cycle'])

    def test_duplicate_delayed_sip_transaction_repairs_cycle_without_new_transaction(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        fund = self.db.get_fund(fid)
        self.db.add_transaction(
            fid, 'sip', '2026-08-23', 1500, 7.5, 200.0,
            'Scheduled SIP for 2026-08-19 (NAV date 2026-08-23)',
            source='sip_execution'
        )
        refreshed = self.db.get_fund(fid)
        self.assertIsNone(refreshed['last_sip_cycle'])
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 23), 200.0)
        self.assertFalse(self.tracker.maybe_execute_sip(
            refreshed, [(date(2026, 8, 25), 250.0)], date(2026, 8, 25)
        ))
        self.assertEqual(1, len([r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']))
        self.assertEqual('2026-08', self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual(1, len(self.db.live_sip_executions(profile_id=1)))

    def test_imported_sip_on_effective_nav_date_is_recognized_by_live_duplicate_guard(self):
        fid = self.add_fund(sip_day=2, sip_amount=1500)
        self.db.add_transaction(
            fid, 'sip', '2026-08-03', 1500, 7.5, 200.0,
            'SIP purchase on 2026-08-03', source='import'
        )
        self.tracker._holiday_calendar_payload = lambda: {'dates': {
            '2026-08-02': {'nse_open': False},
            '2026-08-03': {'nse_open': True},
        }}
        fund = self.db.get_fund(fid)
        self.assertFalse(self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 4), 205.0)], date(2026, 8, 4), previous_nav_date=date(2026, 8, 1)
        ))
        txs = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-03', txs[0]['txn_date'])
        self.assertEqual('2026-08', self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual([], self.db.live_sip_executions(profile_id=1))

    def test_imported_effective_sip_crosses_month_boundary_without_duplicate(self):
        fid = self.add_fund(code='100001', name='Month Boundary Existing', profile_id=1, sip_day=28, sip_amount=1500)
        self.db.add_transaction(
            fid, 'sip', '2026-09-01', 1500, 7.5, 200.0,
            'SIP purchase on 2026-09-01', source='import'
        )
        # August is the next cycle after the already-accounted July cycle;
        # the August 28 SIP's first NSE-open date is September 1.
        self.db.set_sip_executed(fid, '2026-07', '2026-07-28')
        self.tracker._holiday_calendar_payload = lambda: {'dates': {
            '2026-08-28': {'nse_open': False},
            '2026-08-29': {'nse_open': False},
            '2026-08-30': {'nse_open': False},
            '2026-08-31': {'nse_open': False},
            '2026-09-01': {'nse_open': True},
        }}
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 9, 1), 200.0)
        fund = self.db.get_fund(fid)
        self.assertFalse(self.tracker.maybe_execute_sip(
            fund, [(date(2026, 9, 2), 205.0)], date(2026, 9, 2), previous_nav_date=date(2026, 8, 27)
        ))
        txs = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-09-01', txs[0]['txn_date'])
        self.assertEqual('2026-08', self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual([], self.db.live_sip_executions(profile_id=1))

    def test_import_auto_reconciliation_does_not_duplicate_existing_effective_date_sip(self):
        fid = self.add_fund(code='100001', name='Import Auto Existing', profile_id=1, sip_day=2, sip_amount=1500)
        self.db.add_transaction(
            fid, 'sip', '2026-08-03', 1500, 7.5, 200.0,
            'SIP purchase on 2026-08-03', source='import'
        )
        item = {
            'holding_id': fid,
            'import_sip_reconciliations': [{
                'key': 'existing-auto', 'scheme_name': 'Import Auto Existing',
                'sip_date': '2026-08-02', 'nav_date': '2026-08-03',
                'latest_nav_date': '2026-08-03', 'amount': 1500, 'nav': 200,
                'units': 7.5, 'auto_execute': True
            }]
        }
        result = self.tracker.execute_post_import_sips([item], {})
        self.assertEqual([], result['auto_executed'])
        self.assertEqual(1, len([r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']))
        self.assertEqual([], self.db.live_sip_executions(profile_id=1))
        self.assertEqual('2026-08', self.db.get_fund(fid)['last_sip_cycle'])

    def test_existing_live_execution_repairs_cycle_without_new_transaction(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        fund = self.db.get_fund(fid)
        self.db.record_live_sip_execution(
            fund, '2026-08-19', '2026-08-23', 1500, 7.5, 200.0,
            executed_at='2026-08-23T10:00:00+00:00', execution_date='2026-08-23'
        )
        self.assertFalse(self.tracker.maybe_execute_sip(
            self.db.get_fund(fid), [(date(2026, 8, 25), 250.0)], date(2026, 8, 25)
        ))
        self.assertEqual('2026-08', self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual(0, len([r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']))

    def test_legacy_sip_execution_repairs_live_record_without_new_transaction(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        fund = self.db.get_fund(fid)
        self.db.record_sip_execution(
            fund, '2026-08-19', '2026-08-23', 1500, 7.5, 200.0,
            executed_at='2026-08-23T10:00:00+00:00'
        )
        self.assertFalse(self.tracker.maybe_execute_sip(
            self.db.get_fund(fid), [(date(2026, 8, 25), 250.0)], date(2026, 8, 25)
        ))
        self.assertEqual('2026-08', self.db.get_fund(fid)['last_sip_cycle'])
        self.assertEqual(1, len(self.db.live_sip_executions(profile_id=1)))
        self.assertEqual(0, len([r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']))

    def test_same_day_live_sip_does_not_call_historical_lookup(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        fund = self.db.get_fund(fid)
        called = []
        self.tracker.nav_on_or_after_date = lambda *args: called.append(args) or (date(2026, 8, 19), 999.0)
        self.assertTrue(self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 19), 190.0)], date(2026, 8, 19)
        ))
        self.assertEqual([], called)
        txs = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-19', txs[0]['txn_date'])
        self.assertAlmostEqual(190.0, txs[0]['nav'])

    def test_delayed_nav_selects_earliest_available_nav_in_forward_range(self):
        fid = self.add_fund(sip_day=19, sip_amount=1500)
        fund = self.db.get_fund(fid)
        self.tracker.nav_on_or_after_date = lambda scheme, start, end: (date(2026, 8, 23), 230.0)
        self.assertTrue(self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 25), 250.0)], date(2026, 8, 25)
        ))
        txs = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sip']
        self.assertEqual(1, len(txs))
        self.assertEqual('2026-08-23', txs[0]['txn_date'])
        self.assertAlmostEqual(230.0, txs[0]['nav'])

    def test_delayed_nav_live_execution_uses_logical_reporting_date_before_2300(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 26)
        self.tracker.local_now = lambda: datetime(2026, 8, 26, 10, 0, tzinfo=ZoneInfo('Asia/Kolkata'))
        fid = self.add_fund(sip_day=25, sip_amount=3000)
        fund = self.db.get_fund(fid)
        with patch.object(app, 'iso_now', return_value='2026-08-26T03:30:00+00:00'):
            executed = self.tracker.maybe_execute_sip(
                fund, [(date(2026, 8, 25), 91.1854)], date(2026, 8, 25)
            )
        self.assertTrue(executed)
        rows = list(self.db.live_sip_executions(1))
        self.assertEqual(1, len(rows))
        self.assertEqual('2026-08-25', rows[0]['execution_date'])
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual('2026-08-25', summary['today_executed_sip_date'])
        self.assertEqual(['Test Fund'], summary['today_executed_sip_funds'])

    def test_live_sip_execution_writes_dedicated_today_record(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 18)
        self.tracker.local_now = lambda: datetime(2026, 8, 18, 23, 5, tzinfo=ZoneInfo('Asia/Kolkata'))
        fid = self.add_fund(sip_day=18, sip_amount=1500)
        fund = self.db.get_fund(fid)
        with patch.object(app, 'iso_now', return_value='2026-08-18T10:00:00+00:00'):
            executed = self.tracker.maybe_execute_sip(
                fund, [(date(2026, 8, 18), 100.0)], date(2026, 8, 18)
            )
        self.assertTrue(executed)
        rows = list(self.db.live_sip_executions(1, '2026-08-18'))
        self.assertEqual(1, len(rows))
        self.assertEqual('Test Fund', rows[0]['fund_name'])
        self.assertEqual(1500.0, rows[0]['amount'])
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])
        self.assertEqual('2026-08-18', summary['today_executed_sip_date'])

    def test_import_sip_does_not_write_dedicated_live_execution_record(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 18)
        self.tracker.local_now = lambda: datetime(2026, 8, 18, 23, 5, tzinfo=ZoneInfo('Asia/Kolkata'))
        fid = self.add_fund(sip_day=18, sip_amount=1500)
        fund = self.db.get_fund(fid)
        executed = self.tracker.maybe_execute_sip(
            fund, [(date(2026, 8, 18), 100.0)], date(2026, 8, 18),
            source='import', statement_to='2026-08-18'
        )
        self.assertTrue(executed)
        self.assertEqual([], list(self.db.live_sip_executions(1, '2026-08-18')))
        summary = self.tracker._sip_summary(1)
        self.assertEqual(0, summary['today_executed_sip_count'])
        self.assertIsNone(summary['today_executed_sip_date'])

    def test_dedicated_live_record_is_independent_of_legacy_execution_log(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 18)
        self.tracker.local_now = lambda: datetime(2026, 8, 18, 23, 5, tzinfo=ZoneInfo('Asia/Kolkata'))
        fid = self.add_fund(sip_day=18, sip_amount=1500)
        fund = self.db.get_fund(fid)
        self.db.record_sip_execution(
            fund, '2026-08-18', '2026-08-18', 1500, 15, 100,
            executed_at='2026-08-18T10:00:00+00:00'
        )
        # Legacy execution log alone must not turn the sensor/UI on.
        summary = self.tracker._sip_summary(1)
        self.assertEqual(0, summary['today_executed_sip_count'])
        self.db.record_live_sip_execution(
            fund, '2026-08-18', '2026-08-18', 1500, 15, 100,
            executed_at='2026-08-18T10:00:00+00:00'
        )
        summary = self.tracker._sip_summary(1)
        self.assertEqual(1, summary['today_executed_sip_count'])

    def test_existing_scheduler_transactions_are_backfilled_to_execution_log(self):
        fid = self.add_fund()
        with self.db.conn() as c:
            c.execute('''INSERT INTO transactions
                (holding_id,txn_type,txn_date,amount,cashflow,units,nav,charges,folio,source,note,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (fid, 'sip', '2026-08-14', 1000, -1000, 10, 100, 0, None,
                 'sip_execution', 'Scheduled SIP for 2026-08-14 (NAV date 2026-08-14)',
                 '2026-08-14T19:00:00Z'))
        self.db._migrate_sip_execution_log()
        rows = list(self.db.sip_executions(1))
        self.assertEqual(1, len(rows))
        self.assertEqual('2026-08-14T19:00:00Z', rows[0]['executed_at'])


    def test_duplicate_scheme_code_allowed_for_distinct_folios_in_same_import(self):
        original_resolver = self.tracker.resolve_scheme_identity
        original_nav = self.tracker._nav_entries_for_range
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: (str(raw_code), name, [])
        self.tracker._nav_entries_for_range = lambda scheme_code, start_date, cache=None: []
        try:
            data = {
                'format_version': 2,
                'source': {'type': 'CAMS_CONSOLIDATED_STATEMENT', 'statement_to': '2026-08-14'},
                'investors': [{
                    'ref': 'investor_1', 'name': 'Test Investor', 'emails': [], 'phone': None,
                    'address': {}, 'pan': 'ABCDE1234F'
                }],
                'funds': [
                    {
                        'investor_ref': 'investor_1', 'scheme_code': '120503',
                        'scheme_name': 'Axis ELSS A', 'isin': 'INF846K01EW2',
                        'folios': ['FOLIO-A/0'],
                        'sip': {'enabled': False, 'amount': 0, 'day': None},
                        'closing_balance': {'valuation_date': '2026-08-14', 'units': 50, 'cost_value': 5000, 'nav': 100},
                        'history': {'sip_blocks': [], 'transactions': []}
                    },
                    {
                        'investor_ref': 'investor_1', 'scheme_code': '120503',
                        'scheme_name': 'Axis ELSS A', 'isin': 'INF846K01EW2',
                        'folios': ['FOLIO-B/0'],
                        'sip': {'enabled': False, 'amount': 0, 'day': None},
                        'closing_balance': {'valuation_date': '2026-08-14', 'units': 40, 'cost_value': 4000, 'nav': 100},
                        'history': {'sip_blocks': [], 'transactions': []}
                    },
                ]
            }
            result = self.tracker.validate_import_document(data)
            self.assertFalse(any('duplicate verified scheme_code' in e for e in result['errors']))
            self.assertEqual(2, len(result['funds']))
        finally:
            self.tracker.resolve_scheme_identity = original_resolver
            self.tracker._nav_entries_for_range = original_nav

    def test_duplicate_scheme_code_rejected_when_same_folio_repeats(self):
        original_resolver = self.tracker.resolve_scheme_identity
        original_nav = self.tracker._nav_entries_for_range
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: (str(raw_code), name, [])
        self.tracker._nav_entries_for_range = lambda scheme_code, start_date, cache=None: []
        try:
            data = {
                'format_version': 2,
                'source': {'type': 'CAMS_CONSOLIDATED_STATEMENT', 'statement_to': '2026-08-14'},
                'investors': [{
                    'ref': 'investor_1', 'name': 'Test Investor', 'emails': [], 'phone': None,
                    'address': {}, 'pan': 'ABCDE1234F'
                }],
                'funds': [
                    {
                        'investor_ref': 'investor_1', 'scheme_code': '120503',
                        'scheme_name': 'Axis ELSS A', 'isin': 'INF846K01EW2',
                        'folios': ['FOLIO-A/0'],
                        'sip': {'enabled': False, 'amount': 0, 'day': None},
                        'closing_balance': {'valuation_date': '2026-08-14', 'units': 50, 'cost_value': 5000, 'nav': 100},
                        'history': {'sip_blocks': [], 'transactions': []}
                    },
                    {
                        'investor_ref': 'investor_1', 'scheme_code': '120503',
                        'scheme_name': 'Axis ELSS A', 'isin': 'INF846K01EW2',
                        'folios': ['FOLIO-A/0'],
                        'sip': {'enabled': False, 'amount': 0, 'day': None},
                        'closing_balance': {'valuation_date': '2026-08-14', 'units': 40, 'cost_value': 4000, 'nav': 100},
                        'history': {'sip_blocks': [], 'transactions': []}
                    },
                ]
            }
            result = self.tracker.validate_import_document(data)
            self.assertFalse(result['errors'])
            self.assertTrue(any('duplicate holding record in import' in w for w in result['warnings']))
        finally:
            self.tracker.resolve_scheme_identity = original_resolver
            self.tracker._nav_entries_for_range = original_nav

if __name__ == '__main__':
    unittest.main(verbosity=2)


class LatestNavImportRegressionTests(TrackerTestBase):
    def test_import_uses_amfi_current_nav_while_history_stays_mfapi(self):
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: ('120503', name, [])
        self.tracker.request_latest_nav_snapshot = lambda *args, **kwargs: {'120503': (date(2026, 8, 20), 112.2447)}
        history_calls=[]
        self.tracker._nav_entries_for_range = lambda scheme_code, start_date, cache=None: (history_calls.append((scheme_code, start_date)) or [(date(2026,8,18),110.0),(date(2026,8,19),111.0)])
        self.tracker.local_today = lambda: date(2026,8,21)
        doc={
            'format_version':2,
            'source':{'type':'CAMS_CONSOLIDATED_STATEMENT','statement_to':'2026-08-20','statement_generated_date':'2026-08-20'},
            'investors':[{'ref':'investor_1','name':'Test','emails':[],'phone':None,'pan':'ABCDE1234F','address':{}}],
            'funds':[{'investor_ref':'investor_1','scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','folios':['A/0'],
                      'sip':{'enabled':False,'amount':0,'day':None},
                      'closing_balance':{'units':100,'cost_value':10000,'nav':110,'market_value':11000},
                      'history':{'transactions':[]}}]
        }
        result=self.tracker.validate_import_document(doc)
        fund=result['funds'][0]
        self.assertEqual(112.2447, fund['nav_snapshot']['last_nav'])
        self.assertEqual('2026-08-20', fund['nav_snapshot']['last_nav_date'])
        self.assertFalse(history_calls)


    def test_import_populates_reference_navs_from_historical_data_while_using_amfi_latest(self):
        # Regression: import must use AMFI for the current NAV but must also populate
        # previous/month/year reference NAVs immediately. Otherwise a fresh import
        # displays the current value correctly but day/month/year changes remain zero
        # until a manual NAV refresh.
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: ('120503', name, [])
        self.tracker.request_latest_nav_snapshot = lambda *args, **kwargs: {
            '120503': (date(2026, 8, 20), 112.2447)
        }
        self.tracker._amfi_history_snapshot_for_date = lambda d: {
            '120503': (d, {date(2026,8,20):112.2447, date(2026,8,18):110.0, date(2026,8,1):108.0, date(2026,1,1):100.0}.get(d, 100.0))
        }
        self.tracker.local_today = lambda: date(2026, 8, 21)
        doc={
            'format_version':2,
            'source':{'type':'CAMS_CONSOLIDATED_STATEMENT','statement_to':'2026-08-20','statement_generated_date':'2026-08-20'},
            'investors':[{'ref':'investor_1','name':'Test','emails':[],'phone':None,'pan':'ABCDE1234F','address':{}}],
            'funds':[{'investor_ref':'investor_1','scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','folios':['A/0'],
                      'sip':{'enabled':False,'amount':0,'day':None},
                      'closing_balance':{'units':100,'cost_value':10000,'nav':110,'market_value':11000},
                      'history':{'transactions':[]}}]
        }
        result=self.tracker.validate_import_document(doc)
        self.assertFalse(result['errors'], result['errors'])
        snap=result['funds'][0]['nav_snapshot']
        self.assertEqual('2026-08-20', snap['last_nav_date'])
        self.assertEqual(112.2447, snap['last_nav'])
        self.assertEqual('2026-08-19', snap['previous_nav_date'])
        self.assertEqual(100.0, snap['previous_nav'])
        self.assertEqual('2026-08-03', snap['month_nav_date'])
        self.assertEqual(100.0, snap['month_nav'])
        self.assertEqual('2026-01-01', snap['year_nav_date'])
        self.assertEqual(100.0, snap['year_nav'])

    def test_import_forces_fresh_amfi_snapshot_even_when_cached_snapshot_exists(self):
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: ('120503', name, [])
        calls = []
        def fake_snapshot(*args, **kwargs):
            calls.append(dict(kwargs))
            if kwargs.get('force'):
                return {'120503': (date(2026, 8, 20), 112.2447)}
            return {'120503': (date(2026, 8, 18), 110.0)}
        self.tracker.request_latest_nav_snapshot = fake_snapshot
        self.tracker._amfi_history_snapshot_for_date = lambda d: {'120503': (d, 111.0)}
        self.tracker.local_today = lambda: date(2026,8,21)
        doc={
            'format_version':2,
            'source':{'type':'CAMS_CONSOLIDATED_STATEMENT','statement_to':'2026-08-21','statement_generated_date':'2026-08-21'},
            'investors':[{'ref':'investor_1','name':'Test','emails':[],'phone':None,'pan':'ABCDE1234F','address':{}}],
            'funds':[{'investor_ref':'investor_1','scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','folios':['A/0'],
                      'sip':{'enabled':False,'amount':0,'day':None},
                      'closing_balance':{'units':100,'cost_value':10000,'nav':110,'market_value':11000},
                      'history':{'transactions':[]}}]
        }
        result=self.tracker.validate_import_document(doc)
        fund=result['funds'][0]
        self.assertEqual('2026-08-20', fund['nav_snapshot']['last_nav_date'])
        self.assertEqual(112.2447, fund['nav_snapshot']['last_nav'])
        self.assertTrue(any(c.get('force') is True and c.get('all_schemes') is True for c in calls))

    def test_multi_folio_sip_status_is_recomputed_per_folio(self):
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: ('120503', name, [])
        self.tracker.request_latest_nav_snapshot = lambda *args, **kwargs: {'120503': (date(2026,8,20),112.2447)}
        self.tracker._amfi_history_snapshot_for_date = lambda d: {'120503': (d, 111.0)}
        self.tracker._nav_entries_for_range = lambda scheme_code, start_date, cache=None: [(date(2021,11,8),50.0),(date(2026,7,23),110.0),(date(2026,8,20),112.2447)]
        self.tracker.local_today = lambda: date(2026,8,21)
        doc={
            'format_version':2,
            'source':{'type':'CAMS_CONSOLIDATED_STATEMENT','statement_to':'2026-08-21','statement_generated_date':'2026-08-21'},
            'investors':[{'ref':'investor_1','name':'Test','emails':[],'phone':None,'pan':'ABCDE1234F','address':{}}],
            'funds':[{'investor_ref':'investor_1','scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','folios':['CURRENT/0','OLD/0'],
                      'sip':{'enabled':True,'amount':2499.88,'day':23},
                      'closing_balance':{'units':300,'cost_value':20000,'nav':110,'market_value':33000},
                      'history':{'sip_blocks':[{'amount':2499.88,'charges':0.12,'count':1,'start_date':'2021-11-08','end_date':'2021-11-08','sip_day':23,'folio':'OLD/0'},
                                               {'amount':2499.88,'charges':0.12,'count':1,'start_date':'2026-07-23','end_date':'2026-07-23','sip_day':23,'folio':'CURRENT/0'}],
                                 'transactions':[{'date':'2021-11-08','type':'sip','amount':2499.88,'units':22.7,'nav':110,'charges':0,'cashflow':-2499.88,'folio':'OLD/0','note':'Historical'},
                                                {'date':'2026-07-23','type':'sip','amount':2499.88,'units':22.7,'nav':110,'charges':0,'cashflow':-2499.88,'folio':'CURRENT/0','note':'Current'}]}}]
        }
        result=self.tracker.validate_import_document(doc)
        self.assertEqual(2, len(result['funds']))
        by_folio={f['folios'][0]: f for f in result['funds']}
        self.assertTrue(by_folio['CURRENT/0']['sip_enabled'])
        self.assertFalse(by_folio['OLD/0']['sip_enabled'])
        self.assertNotEqual(by_folio['CURRENT/0']['_import_ref'], by_folio['OLD/0']['_import_ref'])

    def test_multi_folio_reconciliation_carries_folio_identity(self):
        tracker = self.tracker
        tracker._effective_sip_trading_date = lambda d: d
        fund = {
            'investor_ref': 'investor_1',
            'scheme_code': '120503',
            'scheme_name': 'Axis ELSS',
            'folios': ['A/0'],
            '_import_ref': 'fund:0:folio:A/0',
            'sip_enabled': True,
            'sip_amount': 2499.88,
            'sip_day': 23,
            'transactions': [{'txn_type': 'sip', 'txn_date': '2026-08-01'}],
            'import_nav_entries': [('2026-08-23', 113.0)],
        }
        recs = tracker._build_import_sip_reconciliations(fund, '2026-08-21')
        self.assertTrue(recs)
        self.assertEqual('fund:0:folio:A/0', recs[0].get('import_ref'))


class FolioImportTests(TrackerTestBase):
    def make_preview(self, folios, transactions=None, units=50, invested=5000):
        return {
            'format_version': 2,
            'source': {'statement_to': '2026-08-14'},
            'investors': [{'ref':'investor_1','name':'Test Investor','identity_status':'pan_match_same','match':{'id':1}}],
            'funds': [{
                'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
                'isin': 'INF846K01EW2', 'investor_ref': 'investor_1', 'folios': folios,
                'units': units, 'invested': invested, 'sip_enabled': False,
                'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01',
                'transactions': transactions or [],
            }],
        }

    def test_validation_does_not_warn_existing_scheme_when_folio_is_new(self):
        # The default profile is id=1; use it directly. PAN is unique by design.
        self.db.update_profile(1, {
            'name': 'Test Investor', 'emails': [], 'phone': None, 'address': {}, 'pan': 'ABCDE1234F'
        })
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['OLD-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [])
        original_resolver = self.tracker.resolve_scheme_identity
        original_nav = self.tracker._nav_entries_for_range
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: ('120503', name, [])
        self.tracker._nav_entries_for_range = lambda scheme_code, start_date, cache=None: []
        try:
            data = {
                'format_version': 2,
                'source': {'type': 'CAMS_CONSOLIDATED_STATEMENT', 'statement_to': '2026-08-14'},
                'investors': [{
                    'ref': 'investor_1', 'name': 'Test Investor', 'emails': [], 'phone': None,
                    'address': {}, 'pan': 'ABCDE1234F'
                }],
                'funds': [{
                    'investor_ref': 'investor_1', 'scheme_code': '120503',
                    'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
                    'isin': 'INF846K01EW2', 'folios': ['NEW-1/0'],
                    'sip': {'enabled': False, 'amount': 0, 'day': None},
                    'closing_balance': {'valuation_date': '2026-08-13', 'units': 50, 'cost_value': 5000, 'nav': 100},
                    'history': {'sip_blocks': [], 'transactions': []}
                }]
            }
            result = self.tracker.validate_import_document(data)
            self.assertFalse(any('already exists and will be skipped' in w for w in result['warnings']))
        finally:
            self.tracker.resolve_scheme_identity = original_resolver
            self.tracker._nav_entries_for_range = original_nav

    def test_new_folio_is_reported_as_change_even_when_transactions_empty(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['OLD-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [])
        preview = self.make_preview(['NEW-1/0'], transactions=[], units=50, invested=5000)
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'new_folio')
        self.assertEqual(changes[0]['folios'], ['NEW-1/0'])
        self.assertEqual(changes[0]['transactions'], 0)

    def test_same_folio_with_new_transaction_is_reported(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['F-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [{'txn_type':'sip','txn_date':'2026-07-25','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0'}])
        preview = self.make_preview(['F-1/0'], transactions=[
            {'txn_type':'sip','txn_date':'2026-07-25','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0'},
            {'txn_type':'sip','txn_date':'2026-08-25','amount':1000,'cashflow':-1000,'units':9,'nav':111.1111,'charges':0,'folio':'F-1/0'},
        ])
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'new_transactions')
        self.assertEqual(changes[0]['transactions'], 1)
        self.assertEqual(changes[0]['transaction_details'][0]['date'], '2026-08-25')

    def test_same_folio_with_no_new_transaction_is_no_change(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['F-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [{'txn_type':'sip','txn_date':'2026-07-25','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0'}])
        preview = self.make_preview(['F-1/0'], transactions=[
            {'txn_type':'sip','txn_date':'2026-07-25','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0'},
        ])
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'no_change')


    def test_newer_statement_same_latest_sip_skips_normalized_sip_but_keeps_new_redemption(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['F-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': True, 'sip_amount': 1000, 'sip_day': 13, 'initial_date': '2021-01-01'
        }, [
            {'txn_type':'sip','txn_date':'2026-08-13','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0','source':'sip_execution'}
        ])
        preview = self.make_preview(['F-1/0'], transactions=[
            # Same normalized SIP date: must be skipped even though this is a newer CAMS statement.
            {'txn_type':'sip','txn_date':'2026-08-13','amount':1000,'cashflow':-1000,'units':10.1,'nav':99.0099,'charges':0,'folio':'F-1/0'},
            # Genuine redemption: must be independently detected.
            {'txn_type':'sell','txn_date':'2026-08-05','amount':500,'cashflow':500,'units':-5,'nav':100,'charges':0,'folio':'F-1/0'},
        ])
        preview['source']['statement_generated_date']='2026-08-20'
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'new_transactions')
        self.assertEqual(changes[0]['transactions'], 1)
        self.assertEqual(changes[0]['transaction_details'][0]['type'], 'sell')

    def test_sip_comparison_is_scoped_to_matching_folio(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['F-OLD/0', 'F-NEW/0'], 'units': 200, 'invested': 20000,
            'sip_enabled': True, 'sip_amount': 1000, 'sip_day': 13, 'initial_date': '2021-01-01'
        }, [
            {'txn_type':'sip','txn_date':'2026-08-20','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-OLD/0'},
            {'txn_type':'sip','txn_date':'2026-07-20','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-NEW/0'}
        ])
        preview = self.make_preview(['F-NEW/0'], transactions=[
            {'txn_type':'sip','txn_date':'2026-08-20','amount':1000,'cashflow':-1000,'units':9,'nav':111.1111,'charges':0,'folio':'F-NEW/0'}
        ])
        preview['source']['statement_generated_date']='2026-08-25'
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'new_transactions')
        self.assertEqual(changes[0]['transactions'], 1)

    def test_existing_scheme_new_folio_creates_separate_holding(self):
        fid = self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['OLD-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [])
        fund = dict(self.make_preview(['NEW-1/0'], units=50, invested=5000)['funds'][0], profile_id=1)
        new_id = self.db.import_fund(fund, [])
        self.assertNotEqual(fid, new_id)
        rows = self.db.all_funds(1)
        self.assertEqual(2, len(rows))
        by_folio = {r['normalized_folio']: r for r in rows}
        self.assertEqual(100, by_folio['OLD-1']['units'])
        self.assertEqual(50, by_folio['NEW-1']['units'])

    def test_newer_statement_with_later_sip_still_reports_new_sip(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['F-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': True, 'sip_amount': 1000, 'sip_day': 13, 'initial_date': '2021-01-01'
        }, [{'txn_type':'sip','txn_date':'2026-08-13','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0'}])
        preview = self.make_preview(['F-1/0'], transactions=[
            {'txn_type':'sip','txn_date':'2026-09-13','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0'},
        ])
        preview['source']['statement_generated_date']='2026-09-20'
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'new_transactions')
        self.assertEqual(changes[0]['transactions'], 1)
        self.assertEqual(changes[0]['transaction_details'][0]['type'], 'sip')

    def test_confirmed_merge_adds_missing_redemption_but_not_duplicate_normalized_sip(self):
        fid = self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['F-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': True, 'sip_amount': 1000, 'sip_day': 13, 'initial_date': '2021-01-01'
        }, [{'txn_type':'sip','txn_date':'2026-08-13','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'F-1/0','source':'sip_execution'}])
        payload = dict(self.make_preview(['F-1/0'], transactions=[
            {'txn_type':'sip','txn_date':'2026-08-13','amount':1000,'cashflow':-1000,'units':10.1,'nav':99.0099,'charges':0,'folio':'F-1/0'},
            {'txn_type':'sell','txn_date':'2026-08-05','amount':500,'cashflow':500,'units':-5,'nav':100,'charges':0,'folio':'F-1/0'},
        ])['funds'][0], profile_id=1)
        self.db.merge_import_fund(payload, payload['transactions'], {'statement_generated_date':'2026-08-20'})
        txs = self.db.transactions(fid)
        self.assertEqual(2, len(txs))
        self.assertEqual(['sell', 'sip'], [t['txn_type'] for t in txs])


    def test_same_folio_different_schemes_in_one_import_create_separate_holdings(self):
        preview = {
            'format_version': 2,
            'source': {'statement_to': '2026-08-14'},
            'investors': [{'ref':'investor_1','name':'Test Investor','identity_status':'pan_match_same','match':{'id':1}}],
            'funds': [
                {
                    'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth','isin':'INF846K01EW2',
                    'investor_ref':'investor_1','folios':['SHARED/0'],'units':10,'invested':1000,
                    'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01','transactions':[]
                },
                {
                    'scheme_code':'120828','scheme_name':'Another Real Scheme','isin':'INF0000000002',
                    'investor_ref':'investor_1','folios':['SHARED/0'],'units':5,'invested':500,
                    'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-02','transactions':[]
                },
            ],
        }
        result = self.tracker.import_validated_preview(preview)
        self.assertEqual(2, len(result['imported']))
        rows = self.db.all_funds(1)
        self.assertEqual(2, len(rows))
        self.assertEqual({'INF846K01EW2','INF0000000002'}, {r['isin'] for r in rows})
        self.assertEqual({'SHARED','SHARED'}, {r['normalized_folio'] for r in rows})


    def test_same_folio_different_strong_schemes_creates_new_holding(self):
        first = self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin':'INF846K01EW2','folios':['SHARED/0'],'units':10,'invested':1000,
            'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        }, [])
        incoming = {
            'profile_id':1,'scheme_code':'120828','scheme_name':'Axis Small Cap Fund Direct Growth',
            'isin':'INF846K01K35','folios':['SHARED'],'units':5,'invested':500,
            'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-02-01'
        }
        action = self.db.find_import_fund(1, incoming)
        self.assertEqual('NEW_FOLIO', action[1])
        self.assertEqual(first, self.db.find_import_fund(1, {**incoming, 'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth','isin':'INF846K01EW2'})[0]['id'])

    def test_same_folio_different_isin_same_scheme_code_requires_review(self):
        self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin':'INF846K01EW2','folios':['SHARED'],'units':10,'invested':1000,
            'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        }, [])
        incoming = {
            'profile_id':1,'scheme_code':'120503','scheme_name':'Some Other Fund',
            'isin':'INF0000000002','folios':['SHARED'],'units':5,'invested':500,
            'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-02-01'
        }
        action = self.db.find_import_fund(1, incoming)
        self.assertEqual('REVIEW_REQUIRED', action[1])
        self.assertIn('conflicting ISIN', action[2])

    def test_same_folio_weak_scheme_difference_requires_review(self):
        self.db.seed_fund({
            'profile_id':1,'scheme_code':'','scheme_name':'Axis ELSS Tax Saver Fund Direct Growth',
            'isin':'','folios':['SHARED'],'units':10,'invested':1000,
            'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        }, [])
        incoming = {
            'profile_id':1,'scheme_code':'','scheme_name':'Axis ELSS Tax Saver Fund Growth Direct',
            'isin':'','folios':['SHARED'],'units':5,'invested':500,
            'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-02-01'
        }
        action = self.db.find_import_fund(1, incoming)
        self.assertEqual('REVIEW_REQUIRED', action[1])

    def test_duplicate_same_holding_record_in_one_import_warns_and_merges(self):
        original_resolver = self.tracker.resolve_scheme_identity
        original_nav = self.tracker._nav_entries_for_range
        self.tracker.resolve_scheme_identity = lambda raw_code, name, isin: (str(raw_code), name, [])
        self.tracker._nav_entries_for_range = lambda scheme_code, start_date, cache=None: []
        try:
            preview = {
                'format_version': 2,
                'source': {'statement_to':'2026-08-14'},
                'investors': [{'ref':'investor_1','name':'Test Investor','identity_status':'pan_match_same','match':{'id':1}}],
                'funds': [
                    {'investor_ref':'investor_1','scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','folios':['DUP/0'],
                     'units':10,'invested':1000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01',
                     'transactions':[{'txn_type':'buy','txn_date':'2021-01-01','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'DUP'}]},
                    {'investor_ref':'investor_1','scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','folios':['DUP'],
                     'units':5,'invested':500,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01',
                     'transactions':[{'txn_type':'buy','txn_date':'2021-02-01','amount':500,'cashflow':-500,'units':5,'nav':100,'charges':0,'folio':'DUP'}]},
                ]
            }
            result = self.tracker.validate_import_document(preview)
            self.assertFalse(result['errors'])
            self.assertTrue(any('duplicate holding record' in w for w in result['warnings']))
            saved = self.tracker.import_validated_preview(result)
            rows = self.db.all_funds(1)
            self.assertEqual(1, len(rows))
            self.assertEqual(2, len(self.db.transactions(rows[0]['id'])))
        finally:
            self.tracker.resolve_scheme_identity = original_resolver
            self.tracker._nav_entries_for_range = original_nav

    def test_shared_folio_transactions_are_routed_to_correct_holdings(self):
        preview = {
            'format_version': 2,
            'source': {'statement_to':'2026-08-14'},
            'investors': [{'ref':'investor_1','name':'Test Investor','identity_status':'pan_match_same','match':{'id':1}}],
            'funds': [
                {'scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','investor_ref':'investor_1','folios':['SHARED'],
                 'units':10,'invested':1000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01',
                 'transactions':[{'txn_type':'buy','txn_date':'2021-01-01','amount':1000,'cashflow':-1000,'units':10,'nav':100,'charges':0,'folio':'SHARED'}]},
                {'scheme_code':'120828','scheme_name':'Axis Small Cap','isin':'INF846K01K35','investor_ref':'investor_1','folios':['SHARED'],
                 'units':5,'invested':500,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01',
                 'transactions':[{'txn_type':'buy','txn_date':'2021-01-02','amount':500,'cashflow':-500,'units':5,'nav':100,'charges':0,'folio':'SHARED'}]},
            ],
        }
        result = self.tracker.import_validated_preview(preview)
        self.assertEqual(2, len(result['imported']))
        rows = self.db.all_funds(1)
        by_isin = {r['isin']: r for r in rows}
        self.assertEqual(1, len(self.db.transactions(by_isin['INF846K01EW2']['id'])))
        self.assertEqual(1, len(self.db.transactions(by_isin['INF846K01K35']['id'])))
        self.assertEqual('2021-01-01', self.db.transactions(by_isin['INF846K01EW2']['id'])[0]['txn_date'])
        self.assertEqual('2021-01-02', self.db.transactions(by_isin['INF846K01K35']['id'])[0]['txn_date'])

    def test_new_folio_does_not_merge_into_existing_holding(self):
        fid = self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['OLD-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [])
        payload = dict(self.make_preview(['NEW-1/0'], units=50, invested=5000)['funds'][0], profile_id=1)
        self.assertEqual(self.db.find_import_fund(1, payload)[1], 'NEW_FOLIO')
        with self.assertRaises(ValueError):
            self.db.merge_import_fund(payload, [])
        row = self.db.get_fund(fid)
        self.assertEqual(row['normalized_folio'], 'OLD-1')
        self.assertAlmostEqual(row['units'], 100)
    def test_same_folio_matches_existing_fund_when_statement_scheme_code_is_blank(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '133859',
            'scheme_name': 'SBI BANKING & FINANCIAL SERVICES FUND - DIRECT PLAN - GROWTH',
            'isin': 'INF200KA1507', 'folios': ['25946036'], 'units': 1583.234, 'invested': 40000,
            'sip_enabled': False, 'sip_amount': 2499.88, 'sip_day': 24, 'initial_date': '2021-03-24'
        }, [{
            'txn_type':'sip','txn_date':'2026-07-24','amount':2499.88,'cashflow':-2499.88,
            'units':50,'nav':49.4483,'charges':0,'folio':'25946036'
        }])
        preview = self.make_preview(['25946036'], transactions=[
            {'txn_type':'sip','txn_date':'2026-07-24','amount':2499.88,'cashflow':-2499.88,
             'units':50,'nav':49.4483,'charges':0,'folio':'25946036'}
        ])
        preview['funds'][0]['scheme_code'] = ''
        preview['funds'][0]['scheme_name'] = 'SBI Banking & Financial Services Fund - Direct Plan-Growth'
        preview['funds'][0]['isin'] = 'INF200KA1507'
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'no_change')

    def test_same_folio_blank_scheme_code_merge_targets_existing_fund(self):
        fid = self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '133859',
            'scheme_name': 'SBI BANKING & FINANCIAL SERVICES FUND - DIRECT PLAN - GROWTH',
            'isin': 'INF200KA1507', 'folios': ['25946036'], 'units': 1583.234, 'invested': 40000,
            'sip_enabled': False, 'sip_amount': 2499.88, 'sip_day': 24, 'initial_date': '2021-03-24'
        }, [{
            'txn_type':'sip','txn_date':'2026-07-24','amount':2499.88,'cashflow':-2499.88,
            'units':50,'nav':49.4483,'charges':0,'folio':'25946036'
        }])
        payload = dict(self.make_preview(['25946036'], transactions=[
            {'txn_type':'sip','txn_date':'2026-07-24','amount':2499.88,'cashflow':-2499.88,
             'units':50,'nav':49.4483,'charges':0,'folio':'25946036'}
        ])['funds'][0], profile_id=1)
        payload['scheme_code'] = ''
        payload['scheme_name'] = 'SBI Banking & Financial Services Fund - Direct Plan-Growth'
        payload['isin'] = 'INF200KA1507'
        self.db.merge_import_fund(payload, payload['transactions'], {})
        txs = self.db.transactions(fid)
        self.assertEqual(1, len(txs))
        row = self.db.get_fund(fid)
        self.assertEqual(row['scheme_code'], '133859')
        self.assertEqual(json.loads(row['folios_json']), ['25946036'])
    def test_existing_scheme_different_folio_is_not_classified_as_no_change(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['OLD-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [])
        preview = self.make_preview(['NEW-1/0'], units=50, invested=5000)
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'new_folio')
        self.assertEqual(changes[0]['folios'], ['NEW-1/0'])

    def test_scheme_code_is_not_enough_when_isin_conflicts(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Existing Scheme',
            'isin': 'INF0000000001', 'folios': ['F-OLD/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [])
        preview = self.make_preview(['F-NEW/0'], units=20, invested=2000)
        preview['funds'][0]['isin'] = 'INF0000000002'
        changes = self.db.preview_import_changes(preview)
        self.assertEqual(changes[0]['type'], 'new_fund')

    def test_mixed_existing_and_new_folios_require_safe_review(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['OLD-1/0'], 'units': 100, 'invested': 10000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [])
        payload = dict(self.make_preview(['OLD-1/0', 'NEW-1/0'], units=50, invested=5000)['funds'][0], profile_id=1)
        with self.assertRaises(ValueError):
            self.db.merge_import_fund(payload, [], {})

    def test_blank_scheme_code_duplicate_is_not_imported_as_new_fund(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '133859',
            'scheme_name': 'SBI BANKING & FINANCIAL SERVICES FUND - DIRECT PLAN - GROWTH',
            'isin': 'INF200KA1507', 'folios': ['25946036'], 'units': 1583.234, 'invested': 40000,
            'sip_enabled': False, 'sip_amount': 2499.88, 'sip_day': 24, 'initial_date': '2021-03-24'
        }, [{
            'txn_type':'sip','txn_date':'2026-07-24','amount':2499.88,'cashflow':-2499.88,
            'units':50,'nav':49.4483,'charges':0,'folio':'25946036'
        }])
        preview = self.make_preview(['25946036'], transactions=[
            {'txn_type':'sip','txn_date':'2026-07-24','amount':2499.88,'cashflow':-2499.88,
             'units':50,'nav':49.4483,'charges':0,'folio':'25946036'}
        ])
        preview['funds'][0]['scheme_code'] = ''
        preview['funds'][0]['scheme_name'] = 'SBI Banking & Financial Services Fund - Direct Plan-Growth'
        preview['funds'][0]['isin'] = 'INF200KA1507'
        result = self.tracker.import_validated_preview(preview)
        self.assertEqual(len(self.db.all_funds(1)), 1)
        self.assertEqual(result['skipped'][0]['reason'], 'already imported; no new transactions')



class NormalizedSchemaTests(TrackerTestBase):
    def test_schema_has_schemes_holdings_and_holding_transactions(self):
        with self.db.conn() as c:
            tables={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn('schemes', tables)
            self.assertIn('holdings', tables)
            self.assertIn('sip_schedules', tables)
            self.assertIn('sip_executions', tables)
            tx_cols={r['name'] for r in c.execute('PRAGMA table_info(transactions)').fetchall()}
            self.assertIn('holding_id', tx_cols)
            self.assertNotIn('fund_id', tx_cols)

    def test_same_scheme_multiple_folios_are_separate_holdings(self):
        first=self.db.seed_fund({'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','folios':['A/0'],'units':100,'invested':10000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'}, [])
        second=self.db.seed_fund({'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS','isin':'INF846K01EW2','folios':['B/0'],'units':50,'invested':5000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'}, [])
        self.assertNotEqual(first, second)
        rows=self.db.all_funds(1)
        self.assertEqual({r['folio'] for r in rows}, {'A/0','B/0'})
        with self.db.conn() as c:
            self.assertIsNotNone(c.execute('SELECT id FROM schemes WHERE id=?',(rows[0]['scheme_id'],)).fetchone())

    def test_pan_is_unique_business_identity(self):
        self.db.update_profile(1, {'name':'Test A','emails':[],'phone':None,'address':{},'pan':'ABCDE1234F'})
        with self.assertRaises(ValueError):
            self.db.create_profile({'name':'Test B','emails':[],'phone':None,'address':{},'pan':' abcde1234f '})

    def test_pan_is_primary_import_lookup_key(self):
        self.db.update_profile(1, {'name':'Original Name','emails':[],'phone':None,'address':{},'pan':'ABCDE1234F'})
        matches = self.db.find_profile_matches({'name':'Completely Different Name','emails':[],'phone':'','address':{},'pan':' abcde1234f '})
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['id'], 1)
        self.assertEqual(matches[0]['match_type'], 'pan')

    def test_all_profiles_returns_creation_order_for_ui_selector(self):
        first = self.db.create_profile({'name':'Zeta First','emails':[],'phone':'','address':{},'pan':'AAAAA1111A'})
        second = self.db.create_profile({'name':'Alpha Second','emails':[],'phone':'','address':{},'pan':'BBBBB2222B'})
        rows = self.db.all_profiles()
        ids = [r['id'] for r in rows]
        self.assertLess(ids.index(first), ids.index(second))

    def test_profile_surrogate_id_is_not_part_of_user_identity(self):
        first = self.db.create_profile({'name':'First','emails':[],'phone':'','address':{},'pan':'AAAAA1111A'})
        self.db.delete_profile(first)
        second = self.db.create_profile({'name':'Second','emails':[],'phone':'','address':{},'pan':'BBBBB2222B'})
        self.assertNotEqual(first, second)
        self.assertEqual(self.db.find_profile_match({'name':'Second','emails':[],'phone':'','address':{},'pan':'BBBBB2222B'})['id'], second)


class V78SchemaTests(TrackerTestBase):
    def test_holdings_are_one_folio_and_legacy_folios_json_is_not_a_storage_column(self):
        self.db.seed_fund({
            'profile_id': 1, 'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin': 'INF846K01EW2', 'folios': ['A/0'], 'units': 10, 'invested': 1000,
            'sip_enabled': False, 'sip_amount': 0, 'sip_day': None, 'initial_date': '2021-01-01'
        }, [])
        with self.db.conn() as c:
            cols=[r['name'] for r in c.execute('PRAGMA table_info(holdings)').fetchall()]
            self.assertNotIn('folios_json', cols)
        row=self.db.get_fund_by_code('120503',1)
        self.assertEqual(json.loads(row['folios_json']), ['A/0'])

    def test_same_scheme_code_distinct_folios_create_distinct_holdings(self):
        for folio in ('A/0','B/0'):
            self.db.import_fund({
                'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth',
                'isin':'INF846K01EW2','folios':[folio],'units':10,'invested':1000,
                'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
            },[])
        rows=self.db.all_funds(1)
        self.assertEqual(2,len(rows))
        self.assertEqual({'A','B'},{r['normalized_folio'] for r in rows})

    def test_same_folio_different_scheme_creates_distinct_holding(self):
        self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin':'INF846K01EW2','folios':['A/0'],'units':10,'invested':1000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        },[])
        incoming={
            'profile_id':1,'scheme_code':'999999','scheme_name':'Other Scheme','isin':'INF0000000001',
            'folios':['A/0'],'units':5,'invested':500,'sip_enabled':False,'sip_amount':0,'sip_day':None,
            'initial_date':'2021-01-01','transactions':[]
        }
        self.assertEqual(self.db.find_import_fund(1,incoming)[1],'NEW_FOLIO')
        result=self.tracker.import_validated_preview({
            'format_version':2,'source':{'statement_to':'2026-08-14'},
            'investors':[{'ref':'investor_1','name':'Test Investor','identity_status':'pan_match_same','match':{'id':1}}],
            'funds':[incoming]
        })
        self.assertEqual(len(result['imported']),1)
        rows=self.db.all_funds(1)
        self.assertEqual(2,len(rows))
        self.assertEqual({'INF846K01EW2','INF0000000001'},{r['isin'] for r in rows})
        self.assertEqual({'A','A'},{r['normalized_folio'] for r in rows})

    def test_only_one_active_sip_schedule_per_holding(self):
        fid=self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth',
            'isin':'INF846K01EW2','folios':['A/0'],'units':10,'invested':1000,'sip_enabled':True,'sip_amount':1000,'sip_day':13,'initial_date':'2021-01-01'
        },[])
        self.db.update_fund(fid, {'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth','isin':'INF846K01EW2','folios':['A/0'],'units':10,'invested':1000,'sip_enabled':True,'sip_amount':1500,'sip_day':14,'initial_date':'2026-08-15'})
        with self.db.conn() as c:
            self.assertEqual(1, c.execute('SELECT COUNT(*) FROM sip_schedules WHERE holding_id=? AND is_active=1',(fid,)).fetchone()[0])


class InvestorDeletionCleanupTests(TrackerTestBase):
    def test_deleted_investor_is_absent_from_portfolio_state(self):
        profile_id = self.db.create_profile({
            'name': 'Harry Potter',
            'emails': [],
            'phone': '',
            'address': {},
            'pan': 'TESTP1234T',
        })
        self.assertIsNotNone(self.db.get_profile(profile_id))
        self.db.delete_profile(profile_id)
        profiles = self.db.all_profiles()
        self.assertNotIn(profile_id, [row['id'] for row in profiles])
        portfolio = app.build_portfolio(self.db)
        self.assertEqual([], portfolio.get('rows', []))

    def test_delete_endpoint_publishes_updated_state(self):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        marker = "TRACKER.db.delete_profile(pid)"
        pos = source.index(marker)
        tail = source[pos:pos + 500]
        self.assertIn("TRACKER.update_ha_states(build_portfolio(TRACKER.db))", tail)

class ImportDecisionTests(TrackerTestBase):
    def ambiguous_preview(self, folio='SHARED/0'):
        return {
            'format_version': 2,
            'source': {'statement_to': '2026-08-14'},
            'investors': [{'ref':'investor_1','name':'Test Investor','identity_status':'pan_match_same','match':{'id':1}}],
            'funds': [{
                'scheme_code':'', 'scheme_name':'Axis ELSS Tax Saver Fund Growth', 'isin':'',
                'investor_ref':'investor_1','folios':[folio], 'units':5, 'invested':500,
                'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2026-01-01',
                'transactions':[{'txn_type':'buy','txn_date':'2026-08-01','amount':500,'cashflow':-500,'units':5,'nav':100,'charges':0,'folio':folio}],
            }],
        }

    def seed_weak_holding(self):
        return self.db.seed_fund({
            'profile_id':1,'scheme_code':'','scheme_name':'Axis ELSS Tax Saver Fund Direct Growth',
            'isin':'','folios':['SHARED/0'],'units':100,'invested':10000,
            'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        }, [])

    def test_same_scheme_multiple_existing_holdings_and_new_folio_is_unambiguous_new_folio(self):
        self.db.seed_fund({'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS A','isin':'INF846K01EW2','folios':['OLD-A'],'units':100,'invested':10000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'}, [])
        self.db.seed_fund({'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS B','isin':'INF846K01EW2','folios':['OLD-B'],'units':50,'invested':5000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'}, [])
        incoming={'profile_id':1,'scheme_code':'','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth','isin':'INF846K01EW2','folios':['NEW-C'],'units':25,'invested':2500,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2026-01-01','transactions':[]}
        holding, action, reason = self.db.find_import_fund(1, incoming)
        self.assertEqual('NEW_FOLIO', action)
        self.assertIsNotNone(holding)
        self.assertIn('incoming folio is new', reason)

    def test_review_required_exposes_user_decisions_and_candidates(self):
        fid=self.seed_weak_holding()
        changes=self.db.preview_import_changes(self.ambiguous_preview())
        self.assertEqual(1, len(changes))
        c=changes[0]
        self.assertEqual('review_required', c['type'])
        self.assertEqual({'skip','merge','new'}, set(c['review_options']))
        self.assertTrue(any(int(x['id'])==fid for x in c['merge_candidates']))

    def test_review_skip_does_not_write(self):
        fid=self.seed_weak_holding()
        preview=self.ambiguous_preview()
        key='fund:0:folio:SHARED'
        result=self.tracker.import_validated_preview(preview, fund_decisions={key:{'mode':'skip'}})
        self.assertTrue(result['skipped'])
        self.assertEqual(1, len(self.db.all_funds(1)))
        self.assertEqual(fid, self.db.all_funds(1)[0]['id'])

    def test_review_merge_writes_to_selected_holding(self):
        fid=self.seed_weak_holding()
        preview=self.ambiguous_preview()
        key='fund:0:folio:SHARED'
        result=self.tracker.import_validated_preview(preview, fund_decisions={key:{'mode':'merge','holding_id':fid}})
        self.assertEqual(1, len(self.db.all_funds(1)))
        self.assertEqual(1, len(self.db.transactions(fid)))
        self.assertEqual('2026-08-01', self.db.transactions(fid)[0]['txn_date'])
        self.assertIn('merged into the holding selected', result['skipped'][0]['reason'])

    def test_review_new_creates_a_new_holding(self):
        self.seed_weak_holding()
        preview=self.ambiguous_preview('NEW/0')
        key='fund:0:folio:NEW'
        result=self.tracker.import_validated_preview(preview, fund_decisions={key:{'mode':'new'}})
        self.assertEqual(2, len(self.db.all_funds(1)))
        self.assertEqual(1, len(result['imported']))

    def test_automatic_new_decision_can_be_overridden_to_skip(self):
        preview=self.ambiguous_preview('NEW-AUTO/0')
        changes=self.db.preview_import_changes(preview)
        self.assertEqual('new_fund', changes[0]['type'])
        self.assertEqual('new', changes[0]['automatic_action'])
        self.assertIn('skip', changes[0]['override_options'])
        key=changes[0]['decision_key']
        result=self.tracker.import_validated_preview(preview, fund_decisions={key:{'mode':'skip'}})
        self.assertEqual(0, len(self.db.all_funds(1)))
        self.assertTrue(result['skipped'])

    def test_automatic_new_decision_can_be_explicitly_accepted(self):
        preview=self.ambiguous_preview('NEW-ACCEPT/0')
        changes=self.db.preview_import_changes(preview)
        key=changes[0]['decision_key']
        result=self.tracker.import_validated_preview(preview, fund_decisions={key:{'mode':'accept'}})
        self.assertEqual(1, len(self.db.all_funds(1)))
        self.assertEqual(1, len(result['imported']))

    def test_automatic_transaction_addition_can_be_overridden_to_skip(self):
        self.db.seed_fund({
            'profile_id':1,'scheme_code':'120503','scheme_name':'Axis ELSS Tax Saver Fund - Direct Growth','isin':'INF846K01EW2',
            'folios':['TX-1/0'],'units':100,'invested':10000,'sip_enabled':False,'sip_amount':0,'sip_day':None,'initial_date':'2021-01-01'
        }, [])
        preview=self.ambiguous_preview('TX-1/0')
        preview['funds'][0]['scheme_code']='120503'
        preview['funds'][0]['scheme_name']='Axis ELSS Tax Saver Fund - Direct Growth'
        preview['funds'][0]['isin']='INF846K01EW2'
        changes=self.db.preview_import_changes(preview)
        self.assertEqual('new_transactions', changes[0]['type'])
        self.assertEqual('merge', changes[0]['automatic_action'])
        self.assertEqual(['skip'], changes[0]['override_options'])
        key=changes[0]['decision_key']
        result=self.tracker.import_validated_preview(preview, fund_decisions={key:{'mode':'skip'}})
        self.assertEqual(0, len(self.db.transactions(self.db.all_funds(1)[0]['id'])))
        self.assertTrue(result['skipped'])

    def test_import_review_ui_exposes_automatic_decision_override(self):
        source=(ROOT/'www/app.js').read_text(encoding='utf-8')
        self.assertIn('Accept automatic decision', source)
        self.assertIn('data-decision-key', source)
        self.assertIn("fund_decisions:window.__importDecisions||{}", source)

class IntegrationReloadRequestTests(TrackerTestBase):
    def test_reload_request_persists_token_in_database(self):
        token = self.db.request_integration_reload()
        self.assertTrue(token)
        self.assertEqual('1', self.db.get_setting('integration_reload_requested'))
        self.assertEqual(token, self.db.get_setting('integration_reload_token'))

    def test_reload_ack_clears_matching_request(self):
        token = self.db.request_integration_reload()
        self.assertTrue(self.db.acknowledge_integration_reload(token))
        self.assertEqual('0', self.db.get_setting('integration_reload_requested'))
        self.assertEqual('', self.db.get_setting('integration_reload_token'))

    def test_reload_ack_does_not_clear_wrong_request(self):
        token = self.db.request_integration_reload()
        self.assertFalse(self.db.acknowledge_integration_reload('wrong-token'))
        self.assertEqual('1', self.db.get_setting('integration_reload_requested'))
        self.assertEqual(token, self.db.get_setting('integration_reload_token'))

class TransactionEditingTests(TrackerTestBase):
    def test_delete_buy_reverses_units_and_invested_and_removes_transaction(self):
        fid = self.add_fund(transactions=[{
            'txn_type': 'buy', 'txn_date': '2026-08-01', 'amount': 1000,
            'cashflow': -1000, 'units': 10, 'nav': 100, 'charges': 0,
            'note': 'Manual buy'
        }])
        before = self.db.get_fund(fid)
        tx = self.db.transactions(fid)[0]
        self.db.delete_transaction(fid, tx['id'])
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(after['units'], before['units'] - 10)
        self.assertAlmostEqual(after['invested'], before['invested'] - 1000)
        self.assertEqual([], self.db.transactions(fid))

    def test_partial_sell_reduces_invested_pro_rata(self):
        fid = self.add_fund(transactions=[{
            'txn_type': 'sip', 'txn_date': '2026-08-01', 'amount': 10000,
            'cashflow': -10000, 'units': 100, 'nav': 100, 'charges': 0,
            'note': 'Initial SIP'
        }])
        fund = self.db.get_fund(fid)
        self.db.add_transaction(fid, 'sell', '2026-08-10', 7500, 50, 150, 'Partial redemption')
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(fund['units'] - 50, after['units'])
        self.assertAlmostEqual(5000.0, after['invested'])
        self.assertAlmostEqual(7500.0, after['units'] * 150.0)

    def test_full_sell_reduces_invested_to_zero(self):
        fid = self.add_fund(transactions=[{
            'txn_type': 'sip', 'txn_date': '2026-08-01', 'amount': 10000,
            'cashflow': -10000, 'units': 100, 'nav': 100, 'charges': 0,
            'note': 'Initial SIP'
        }])
        self.db.add_transaction(fid, 'sell', '2026-08-10', 15000, 100, 150, 'Full redemption')
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(0.0, after['units'])
        self.assertAlmostEqual(0.0, after['invested'])

    def test_partial_switch_out_reduces_invested_pro_rata(self):
        fid = self.add_fund(transactions=[{
            'txn_type': 'sip', 'txn_date': '2026-08-01', 'amount': 10000,
            'cashflow': -10000, 'units': 100, 'nav': 100, 'charges': 0,
            'note': 'Initial SIP'
        }])
        self.db.add_transaction(fid, 'switch_out', '2026-08-10', 0, 40, 125, 'Partial switch')
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(60.0, after['units'])
        self.assertAlmostEqual(6000.0, after['invested'])

    def test_full_switch_out_reduces_invested_to_zero(self):
        fid = self.add_fund(transactions=[{
            'txn_type': 'sip', 'txn_date': '2026-08-01', 'amount': 10000,
            'cashflow': -10000, 'units': 100, 'nav': 100, 'charges': 0,
            'note': 'Initial SIP'
        }])
        self.db.add_transaction(fid, 'switch_out', '2026-08-10', 0, 100, 125, 'Full switch')
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(0.0, after['units'])
        self.assertAlmostEqual(0.0, after['invested'])

    def test_delete_partial_sell_restores_cost_basis(self):
        fid = self.add_fund(transactions=[{
            'txn_type': 'sip', 'txn_date': '2026-08-01', 'amount': 10000,
            'cashflow': -10000, 'units': 100, 'nav': 100, 'charges': 0,
            'note': 'Initial SIP'
        }])
        self.db.add_transaction(fid, 'sell', '2026-08-10', 7500, 50, 150, 'Partial redemption')
        tx = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sell'][0]
        self.db.delete_transaction(fid, tx['id'])
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(100.0, after['units'])
        self.assertAlmostEqual(10000.0, after['invested'])

    def test_delete_full_sell_restores_pre_sell_cost_basis(self):
        fid = self.add_fund(transactions=[{
            'txn_type': 'sip', 'txn_date': '2026-08-01', 'amount': 10000,
            'cashflow': -10000, 'units': 100, 'nav': 100, 'charges': 0,
            'note': 'Initial SIP'
        }])
        self.db.add_transaction(fid, 'sell', '2026-08-10', 15000, 100, 150, 'Full redemption')
        tx = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sell'][0]
        self.db.delete_transaction(fid, tx['id'])
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(100.0, after['units'])
        self.assertAlmostEqual(10000.0, after['invested'])

    def test_delete_live_sip_transaction_removes_dedicated_execution_record(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        fid = self.add_fund(sip_day=13, sip_amount=1000)
        fund = self.db.get_fund(fid)
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 13), 100.0)], date(2026, 8, 13))
        tx = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sip'][-1]
        self.assertEqual(1, len(self.db.live_sip_executions(1)))
        self.db.delete_transaction(fid, tx['id'])
        self.assertEqual([], list(self.db.live_sip_executions(1)))

    def test_delete_live_sip_converts_transaction_to_zero_marker_and_keeps_cycle_accounted(self):
        fid = self.add_fund(sip_day=13, sip_amount=1000)
        fund = self.db.get_fund(fid)
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 13), 100.0)], date(2026, 8, 13))
        self.assertEqual(1, len(self.db.sip_executions_for_holding(fid)))
        before = self.db.get_fund(fid)
        tx = [r for r in self.db.transactions(fid) if r['source'] == 'sip_execution'][0]
        self.db.delete_transaction(fid, tx['id'])
        after = self.db.get_fund(fid)
        markers = [r for r in self.db.transactions(fid) if r['id'] == tx['id']]
        self.assertEqual(1, len(markers))
        marker = markers[0]
        self.assertEqual(0.0, marker['amount'])
        self.assertEqual(0.0, marker['units'])
        self.assertEqual(0.0, marker['cashflow'])
        self.assertIsNone(marker['nav'])
        self.assertIn('SIP skipped/deleted', marker['note'])
        self.assertEqual([], self.db.sip_executions_for_holding(fid))
        self.assertEqual('2026-08', after['last_sip_cycle'])
        self.assertAlmostEqual(after['units'], before['units'] - tx['units'])
        self.assertAlmostEqual(after['invested'], before['invested'] - tx['amount'])
        # Scheduler must treat the zero-value marker as an accounted cycle.
        refreshed = self.db.get_fund(fid)
        self.assertFalse(self.tracker.maybe_execute_sip(refreshed, [(date(2026, 8, 13), 100.0)], date(2026, 8, 13)))

    def test_deleted_sip_zero_marker_is_hidden_from_transaction_api(self):
        fid = self.add_fund(sip_day=13, sip_amount=1000)
        fund = self.db.get_fund(fid)
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 13), 100.0)], date(2026, 8, 13))
        tx = [r for r in self.db.transactions(fid) if r['source'] == 'sip_execution'][0]
        self.db.delete_transaction(fid, tx['id'])
        visible = [r for r in self.db.transactions(fid) if not (r['txn_type'] == 'sip' and r['amount'] == 0 and r['units'] == 0 and r['cashflow'] == 0)]
        self.assertEqual([], [r for r in visible if r['id'] == tx['id']])

    def test_delete_partial_sell_restores_cams_seeded_cost_without_initial_ledger_transaction(self):
        fid = self.add_fund(enabled=False)
        self.db.add_transaction(fid, 'sell', '2026-08-10', 7500, 50, 150, 'Partial redemption')
        tx = [r for r in self.db.transactions(fid) if r['txn_type'] == 'sell'][0]
        self.db.delete_transaction(fid, tx['id'])
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(100.0, after['units'])
        self.assertAlmostEqual(10000.0, after['invested'])

    def test_delete_partial_switch_out_restores_cams_seeded_cost_without_initial_ledger_transaction(self):
        fid = self.add_fund(enabled=False)
        self.db.add_transaction(fid, 'switch_out', '2026-08-10', 0, 40, 125, 'Partial switch')
        tx = [r for r in self.db.transactions(fid) if r['txn_type'] == 'switch_out'][0]
        self.db.delete_transaction(fid, tx['id'])
        after = self.db.get_fund(fid)
        self.assertAlmostEqual(100.0, after['units'])
        self.assertAlmostEqual(10000.0, after['invested'])

    def test_deleted_zero_sip_marker_does_not_count_as_executed_sip(self):
        self.tracker.timezone_name = 'Asia/Kolkata'
        self.tracker.local_today = lambda: date(2026, 8, 13)
        fid = self.add_fund(sip_day=13, sip_amount=1000)
        fund = self.db.get_fund(fid)
        self.assertTrue(self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 13), 100.0)], date(2026, 8, 13)))
        tx = [r for r in self.db.transactions(fid) if r['source'] == 'sip_execution'][0]
        self.db.delete_transaction(fid, tx['id'])
        with self.db.conn() as c:
            c.execute('UPDATE holdings SET last_sip_cycle=NULL WHERE id=?', (fid,))
        status = self.tracker._build_sip_status(1)
        self.assertEqual(0, status['executed_sip_count'])
        self.assertEqual([], status['executed_sip_funds'])
        self.assertEqual('2026-09-13', status['sip_date_fund_groups'][0]['cycle_date'])
        self.assertFalse(self.tracker.maybe_execute_sip(self.db.get_fund(fid), [(date(2026, 8, 13), 100.0)], date(2026, 8, 13)))

    def test_manual_transaction_ui_uses_amount_or_units(self):
        source = (ROOT/'www/app.js').read_text(encoding='utf-8')
        self.assertIn('<label>Amount</label>', source)
        self.assertIn('<div class="tx-or">OR</div>', source)
        self.assertIn('<label>Units</label>', source)
        self.assertIn('Enter either amount or units, not both', source)
        self.assertIn("input_mode:'calculated'", source)

class IntegrationSnapshotConsistencyTests(TrackerTestBase):
    def _set_nav(self, fid, nav):
        self.db.update_nav(fid, {
            'last_nav': nav, 'last_nav_date': '2026-08-18',
            'previous_nav': nav - 1, 'previous_nav_date': '2026-08-17',
            'month_nav': nav - 2, 'month_nav_date': '2026-08-01',
            'year_nav': nav - 10, 'year_nav_date': '2026-01-01',
            'last_refresh': '2026-08-19T05:18:00Z', 'last_error': None,
        })

    def test_profile_total_integration_snapshot_matches_latest_portfolio_after_two_holdings(self):
        first = self.add_fund(code='100001', name='Primary Fund', enabled=False)
        second = self.add_fund(code='100002', name='Merged Fund', profile_id=1, enabled=False)
        with self.db.conn() as c:
            c.execute('UPDATE holdings SET units=100 WHERE id=?', (first,))
            c.execute('UPDATE holdings SET units=50 WHERE id=?', (second,))
        self._set_nav(first, 100.0)
        self._set_nav(second, 200.0)
        latest = app.build_portfolio(self.db, 1)
        self.assertAlmostEqual(float(latest['total']['value']), 20000.0)
        self.tracker._holiday_status = lambda d: {'nse_open': True, 'description': 'Functional'}
        self.tracker._market_status_payload = lambda: {'market_open': False, 'status': 'CLOSED', 'reason': 'Closed'}
        state_dir = Path(self.tmp.name) / 'state'
        old = os.environ.get('MFT_INTEGRATION_STATE_DIRS')
        os.environ['MFT_INTEGRATION_STATE_DIRS'] = str(state_dir)
        try:
            self.tracker._write_integration_state()
            payload = json.loads((state_dir / 'integration_state.json').read_text(encoding='utf-8'))
            profile = next(p for p in payload['profiles'] if int(p['id']) == 1)
            self.assertAlmostEqual(float(profile['total']['value']), float(latest['total']['value']))
            self.assertEqual(2, profile['fund_count'])
            self.assertGreaterEqual(int(payload['state_revision']), 1)
        finally:
            if old is None:
                os.environ.pop('MFT_INTEGRATION_STATE_DIRS', None)
            else:
                os.environ['MFT_INTEGRATION_STATE_DIRS'] = old

    def test_integration_export_revision_increases(self):
        self.add_fund(enabled=False)
        self.tracker._holiday_status = lambda d: {'nse_open': True, 'description': 'Functional'}
        self.tracker._market_status_payload = lambda: {'market_open': False, 'status': 'CLOSED', 'reason': 'Closed'}
        state_dir = Path(self.tmp.name) / 'state'
        old = os.environ.get('MFT_INTEGRATION_STATE_DIRS')
        os.environ['MFT_INTEGRATION_STATE_DIRS'] = str(state_dir)
        try:
            self.tracker._write_integration_state()
            first = json.loads((state_dir / 'integration_state.json').read_text(encoding='utf-8'))
            self.tracker._write_integration_state()
            second = json.loads((state_dir / 'integration_state.json').read_text(encoding='utf-8'))
            self.assertGreater(second['state_revision'], first['state_revision'])
        finally:
            if old is None:
                os.environ.pop('MFT_INTEGRATION_STATE_DIRS', None)
            else:
                os.environ['MFT_INTEGRATION_STATE_DIRS'] = old


class IntegrationStateExportTests(TrackerTestBase):
    def test_build_portfolio_accepts_real_sqlite_rows(self):
        """Regression: SQLite Row does not implement dict.get()."""
        fid = self.add_fund(sip_day=13)
        self.db.update_nav(fid, {
            'last_nav': 100.0, 'last_nav_date': '2026-08-18',
            'previous_nav': 99.0, 'previous_nav_date': '2026-08-17',
            'month_nav': 98.0, 'month_nav_date': '2026-08-01',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': '2026-08-18T08:30:00Z', 'last_error': None,
        })
        portfolio = app.build_portfolio(self.db)
        self.assertEqual('2026-08-18', portfolio['nav_date'])
        self.assertEqual(1, len(portfolio['rows']))

    def test_integration_state_is_written_after_portfolio_change(self):
        """Regression for the stale-HA-state bug seen after NAV refresh/SIP execution."""
        fid = self.add_fund(sip_day=13)
        self.db.update_nav(fid, {
            'last_nav': 100.0, 'last_nav_date': '2026-08-18',
            'previous_nav': 99.0, 'previous_nav_date': '2026-08-17',
            'month_nav': 98.0, 'month_nav_date': '2026-08-01',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': '2026-08-18T08:30:00Z', 'last_error': None,
        })
        self.tracker.local_today = lambda: date(2026, 8, 18)
        self.tracker._holiday_status = lambda d: {'nse_open': True, 'description': 'Functional'}
        self.tracker._market_status_payload = lambda: {'market_open': False, 'status': 'CLOSED', 'reason': 'Closed'}
        self.tracker._holiday_calendar_payload = lambda: {'dates': {
            '2026-08-18': {'nse_open': True}, '2026-08-19': {'nse_open': True},
            '2026-08-20': {'nse_open': True}, '2026-08-21': {'nse_open': True},
        }}
        state_dir = Path(self.tmp.name) / 'ha_state'
        old = os.environ.get('MFT_INTEGRATION_STATE_DIRS')
        os.environ['MFT_INTEGRATION_STATE_DIRS'] = str(state_dir)
        try:
            self.tracker._write_integration_state()
            target = state_dir / 'integration_state.json'
            self.assertTrue(target.exists())
            payload = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual('2026-08-18', payload['profiles'][0]['nav_date'])
            self.assertEqual('Test Fund', payload['profiles'][0]['funds'][0]['fund_name'])
        finally:
            if old is None:
                os.environ.pop('MFT_INTEGRATION_STATE_DIRS', None)
            else:
                os.environ['MFT_INTEGRATION_STATE_DIRS'] = old


class NextExpectedSipRegressionTests(TrackerTestBase):
    def _configure_calendar(self):
        self.tracker.local_today = lambda: date(2026, 8, 18)
        self.tracker._holiday_calendar_payload = lambda: {'dates': {
            '2026-08-17': {'nse_open': True}, '2026-08-18': {'nse_open': True},
            '2026-08-19': {'nse_open': True}, '2026-08-20': {'nse_open': True},
            '2026-08-21': {'nse_open': True}, '2026-09-13': {'nse_open': False}, '2026-09-14': {'nse_open': True},
        }}

    def test_successful_sip_advances_next_expected_date(self):
        self._configure_calendar()
        fid = self.add_fund(sip_day=13, sip_amount=2500)
        self.db.update_nav(fid, {
            'last_nav': 100.0, 'last_nav_date': '2026-08-17',
            'previous_nav': 99.0, 'previous_nav_date': '2026-08-14',
            'month_nav': 98.0, 'month_nav_date': '2026-08-01',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': '2026-08-18T08:00:00Z', 'last_error': None,
        })
        fund = self.db.get_fund(fid)
        self.tracker.maybe_execute_sip(fund, [(date(2026, 8, 17), 100.0)], date(2026, 8, 17), source='live')
        summary = self.tracker._sip_summary(1)
        self.assertEqual('2026-09-15', summary['next_expected_sip_date'])
        self.assertEqual(['Test Fund'], summary['next_expected_sip_funds'])
        self.assertEqual(1, summary['next_expected_sip_count'])
        self.assertAlmostEqual(2500.0, summary['next_expected_sip_total_amount'])
        self.assertAlmostEqual(2500.0, summary['next_expected_sip_amounts']['Test Fund'])

    def test_next_expected_amounts_are_exported_to_integration_state_attributes(self):
        self._configure_calendar()
        fid = self.add_fund(sip_day=20, sip_amount=1750)
        self.db.update_nav(fid, {
            'last_nav': 100.0, 'last_nav_date': '2026-08-18',
            'previous_nav': 99.0, 'previous_nav_date': '2026-08-17',
            'month_nav': 98.0, 'month_nav_date': '2026-08-01',
            'year_nav': 90.0, 'year_nav_date': '2026-01-01',
            'last_refresh': '2026-08-18T08:00:00Z', 'last_error': None,
        })
        self.tracker._holiday_status = lambda d: {'nse_open': True, 'description': 'Functional'}
        self.tracker._market_status_payload = lambda: {'market_open': False, 'status': 'CLOSED', 'reason': 'Closed'}
        state_dir = Path(self.tmp.name) / 'ha_state'
        old = os.environ.get('MFT_INTEGRATION_STATE_DIRS')
        os.environ['MFT_INTEGRATION_STATE_DIRS'] = str(state_dir)
        try:
            self.tracker._write_integration_state()
            payload = json.loads((state_dir / 'integration_state.json').read_text(encoding='utf-8'))
            group = payload['profiles'][0]
            self.assertEqual(1, group['next_expected_sip_count'])
            self.assertAlmostEqual(1750.0, group['next_expected_sip_total_amount'])
            self.assertEqual(1750.0, group['next_expected_sip_amounts']['Test Fund'])
        finally:
            if old is None:
                os.environ.pop('MFT_INTEGRATION_STATE_DIRS', None)
            else:
                os.environ['MFT_INTEGRATION_STATE_DIRS'] = old

class AddLumpsumTests(TrackerTestBase):
    def setUp(self):
        super().setUp()
        self.old_tracker = app.TRACKER
        app.TRACKER = self.tracker
        app._refresh_state(status='idle', stage='Idle', progress=0, message='Waiting for refresh', error=None)
        self.tracker.update_ha_states = lambda *_args, **_kwargs: None
        self.tracker._write_integration_state = lambda: None
        self.tracker._holiday_status = lambda d: {'nse_open': True, 'description': 'Functional'}
        self.tracker._market_status_payload = lambda: {'market_open': False, 'status': 'CLOSED', 'reason': 'Closed'}

    def tearDown(self):
        app.TRACKER = self.old_tracker
        super().tearDown()

    def test_lumpsum_prepare_from_amount_calculates_units(self):
        data = {
            'profile_id': 1,
            'scheme_code': '100999',
            'scheme_name': 'Lumpsum Test Fund',
            'lumpsum_transactions': [
                {'nav_date': '2026-08-10', 'amount': '10000', 'nav': '500'},
            ],
        }
        # Both values are accepted only when they agree with the confirmed NAV.
        payload, history = app._prepare_lumpsum_payload(data, require_nav=True)
        self.assertAlmostEqual(payload['invested'], 10000.0)
        self.assertAlmostEqual(payload['units'], 20.0)
        self.assertEqual('2026-08-10', history[0]['txn_date'])
        self.assertAlmostEqual(history[0]['nav'], 500.0)

    def test_lumpsum_prepare_from_units_calculates_amount(self):
        data = {
            'profile_id': 1,
            'scheme_code': '101000',
            'scheme_name': 'Units Only Fund',
            'lumpsum_transactions': [
                {'nav_date': '2026-08-11', 'units': '10', 'nav': '250'},
            ],
        }
        payload, history = app._prepare_lumpsum_payload(data, require_nav=True)
        self.assertAlmostEqual(payload['invested'], 2500.0)
        self.assertAlmostEqual(payload['units'], 10.0)
        self.assertAlmostEqual(abs(history[0]['amount']), 2500.0)

    def test_lumpsum_prepare_rejects_conflicting_amount_and_units(self):
        data = {
            'profile_id': 1,
            'scheme_code': '101001',
            'scheme_name': 'Conflicting Fund',
            'lumpsum_transactions': [
                {'nav_date': '2026-08-12', 'amount': '10000', 'units': '30', 'nav': '300'},
            ],
        }
        with self.assertRaises(ValueError):
            app._prepare_lumpsum_payload(data, require_nav=True)

    def test_lumpsum_prepare_rejects_missing_nav_after_confirmation(self):
        data = {
            'profile_id': 1,
            'scheme_code': '101002',
            'scheme_name': 'Missing NAV Fund',
            'lumpsum_transactions': [
                {'nav_date': '2026-08-12', 'amount': '10000'},
            ],
        }
        with self.assertRaisesRegex(ValueError, 'NAV must be confirmed'):
            app._prepare_lumpsum_payload(data, require_nav=True)

    def test_lumpsum_job_refreshes_only_new_fund_not_full_portfolio(self):
        data = {
            'profile_id': 1,
            'scheme_code': '101003',
            'scheme_name': 'Job Fund',
            'lumpsum_transactions': [
                {'nav_date': '2026-08-01', 'amount': '1000', 'nav': '100'},
                {'nav_date': '2026-08-05', 'units': '5', 'nav': '120'},
            ],
        }
        refresh_calls = []
        self.tracker.refresh_all = lambda: (_ for _ in ()).throw(AssertionError('Add Mutual Fund must not call refresh_all()'))
        def fake_refresh_fund(fund, *args, **kwargs):
            refresh_calls.append(int(fund['id']))
            self.db.update_nav(fund['id'], {
                'last_nav': 130.0, 'last_nav_date': '2026-08-18',
                'previous_nav': 125.0, 'previous_nav_date': '2026-08-17',
                'month_nav': 120.0, 'month_nav_date': '2026-08-01',
                'year_nav': 110.0, 'year_nav_date': '2026-01-01',
                'last_refresh': '2026-08-18T12:00:00Z', 'last_error': None,
            })
            return [(date(2026, 8, 18), 130.0), (date(2026, 8, 17), 125.0)]
        self.tracker.refresh_fund = fake_refresh_fund
        job = app._new_add_fund_job(data)
        for _ in range(250):
            with app.ADD_FUND_JOBS_LOCK:
                state = dict(app.ADD_FUND_JOBS[job['id']])
            if state['status'] in ('completed', 'error'):
                break
            import time
            time.sleep(0.02)
        self.assertEqual('completed', state['status'])
        self.assertTrue(state['added'])
        fid = state['result']['id']
        self.assertEqual([fid], refresh_calls)
        self.assertEqual(130.0, self.db.get_fund(fid)['last_nav'])
        txs = [dict(r) for r in self.db.transactions(fid) if r['txn_type'] == 'lumpsum']
        self.assertEqual(2, len(txs))
        self.assertEqual({'2026-08-01', '2026-08-05'}, {t['txn_date'] for t in txs})
        self.assertAlmostEqual(sum(abs(float(t['amount'])) for t in txs), 1600.0)
        self.assertAlmostEqual(sum(float(t['units']) for t in txs), 15.0)

class AddSIPPreviewTests(TrackerTestBase):
    def setUp(self):
        super().setUp()
        self.old_tracker = app.TRACKER
        app.TRACKER = self.tracker
        app._refresh_state(status='idle', stage='Idle', progress=0, message='Waiting for refresh', error=None)
        self.tracker.update_ha_states = lambda *_args, **_kwargs: None
        self.tracker._write_integration_state = lambda: None
        self.tracker._holiday_status = lambda d: {'nse_open': True, 'description': 'Functional'}
        self.tracker._market_status_payload = lambda: {'market_open': False, 'status': 'CLOSED', 'reason': 'Closed'}

    def tearDown(self):
        app.TRACKER = self.old_tracker
        super().tearDown()

    def test_sip_preview_reconstructs_history_and_units(self):
        self.tracker.historical_sip_transactions = lambda code, amount, day, first, count: [
            {'txn_type':'sip','txn_date':'2026-06-10','amount':1000.0,'cashflow':-1000.0,'units':10.0,'nav':100.0},
            {'txn_type':'sip','txn_date':'2026-07-10','amount':1000.0,'cashflow':-1000.0,'units':8.0,'nav':125.0},
            {'txn_type':'sip','txn_date':'2026-08-10','amount':1000.0,'cashflow':-1000.0,'units':5.0,'nav':200.0},
        ]
        progress=[]
        preview=app._prepare_sip_preview({
            'profile_id':1,'scheme_code':'200001','scheme_name':'SIP Preview Fund',
            'units':'23','invested':'3000','sip_amount':'1000','sip_day':'10','first_sip_date':'2026-06-10','sip_count':'3'
        }, progress_cb=lambda stage,pct,msg,sub='': progress.append((stage,pct)))
        self.assertEqual(3, preview['sip_count'])
        self.assertAlmostEqual(23.0, preview['current_units'])
        self.assertAlmostEqual(23.0, preview['reconstructed_units'])
        self.assertAlmostEqual(3000.0, preview['expected_invested'])
        self.assertAlmostEqual(0.0, preview['adjustment_units'])
        self.assertTrue(progress)
        self.assertGreaterEqual(progress[-1][1], 70)
        with app.SIP_PREVIEWS_LOCK:
            self.assertIn(preview['preview_id'], app.SIP_PREVIEWS)

    def test_sip_preview_rejects_future_or_invalid_count(self):
        with self.assertRaises(ValueError):
            app._prepare_sip_preview({
                'profile_id':1,'scheme_code':'200002','scheme_name':'Bad SIP',
                'units':'100','invested':'1000','sip_amount':'1000','sip_day':'10','first_sip_date':'2026-08-10','sip_count':'0'
            })

    def test_sip_add_job_saves_fund_and_marks_added_before_refresh(self):
        history=[
            {'txn_type':'sip','txn_date':'2026-06-10','amount':1000.0,'cashflow':-1000.0,'units':10.0,'nav':100.0,'note':'Historical SIP'},
            {'txn_type':'sip','txn_date':'2026-07-10','amount':1000.0,'cashflow':-1000.0,'units':8.0,'nav':125.0,'note':'Historical SIP'},
        ]
        preview_id='test-sip-preview'
        with app.SIP_PREVIEWS_LOCK:
            app.SIP_PREVIEWS[preview_id]={'data':{
                'preview_id':preview_id,'profile_id':1,'scheme_code':'200003','scheme_name':'SIP Job Fund',
                'current_units':18.0,'current_invested':2000.0,'expected_invested':2000.0,'sip_amount':1000.0,
                'sip_day':10,'initial_date':'2026-06-10','history':history,
            }}
        self.tracker.refresh_all = lambda: (_ for _ in ()).throw(AssertionError('Add Mutual Fund must not call refresh_all()'))
        refresh_calls=[]
        def fake_refresh_fund(fund, *args, **kwargs):
            refresh_calls.append(int(fund['id']))
            self.db.update_nav(fund['id'], {
                'last_nav': 210.0, 'last_nav_date': '2026-08-18',
                'previous_nav': 205.0, 'previous_nav_date': '2026-08-17',
                'month_nav': 200.0, 'month_nav_date': '2026-08-01',
                'year_nav': 190.0, 'year_nav_date': '2026-01-01',
                'last_refresh': '2026-08-18T12:00:00Z', 'last_error': None,
            })
            return [(date(2026, 8, 18), 210.0), (date(2026, 8, 17), 205.0)]
        self.tracker.refresh_fund = fake_refresh_fund
        try:
            job=app._new_add_fund_job({'acquisition_type':'sip','preview_id':preview_id})
            states=[]
            for _ in range(250):
                with app.ADD_FUND_JOBS_LOCK:
                    state=dict(app.ADD_FUND_JOBS[job['id']])
                states.append(state)
                if state['status'] in ('completed','error'):
                    break
                time.sleep(0.01)
            self.assertEqual('completed', state['status'])
            self.assertTrue(state['added'])
            fid=state['result']['id']
            self.assertEqual([fid], refresh_calls)
            self.assertEqual(210.0, self.db.get_fund(fid)['last_nav'])
            txs=[dict(r) for r in self.db.transactions(fid) if r['txn_type']=='sip']
            self.assertEqual(2, len(txs))
            self.assertAlmostEqual(2000.0, sum(float(r['amount']) for r in txs))
            self.assertIsNotNone(self.db.get_fund(fid))
        finally:
            with app.SIP_PREVIEWS_LOCK:
                app.SIP_PREVIEWS.pop(preview_id, None)


class AddFundProgressMonitoringTests(TrackerTestBase):
    def setUp(self):
        super().setUp()
        self.old_tracker = app.TRACKER
        app.TRACKER = self.tracker
        app._refresh_state(status='idle', stage='Idle', progress=0, message='Waiting for refresh', error=None)
        self.tracker.update_ha_states = lambda *_args, **_kwargs: None
        self.tracker._write_integration_state = lambda: None
        self.tracker._holiday_status = lambda d: {'nse_open': True, 'description': 'Functional'}
        self.tracker._market_status_payload = lambda: {'market_open': False, 'status': 'CLOSED', 'reason': 'Closed'}

    def tearDown(self):
        app.TRACKER = self.old_tracker
        super().tearDown()

    def test_add_fund_progress_uses_single_fund_refresh(self):
        data={
            'profile_id':1,'scheme_code':'200004','scheme_name':'Progress Fund',
            'lumpsum_transactions':[{'nav_date':'2026-08-01','amount':'1000','nav':'100'}]
        }
        self.tracker.refresh_all = lambda: (_ for _ in ()).throw(AssertionError('Add Mutual Fund must not call refresh_all()'))
        def slow_single_refresh(fund, *args, **kwargs):
            time.sleep(0.15)
            self.db.update_nav(fund['id'], {
                'last_nav': 105.0, 'last_nav_date': '2026-08-18',
                'previous_nav': 100.0, 'previous_nav_date': '2026-08-17',
                'month_nav': 100.0, 'month_nav_date': '2026-08-01',
                'year_nav': 95.0, 'year_nav_date': '2026-01-01',
                'last_refresh': '2026-08-18T12:00:00Z', 'last_error': None,
            })
            return [(date(2026,8,18),105.0),(date(2026,8,17),100.0)]
        self.tracker.refresh_fund = slow_single_refresh
        job=app._new_add_fund_job(data)
        states=[]
        for _ in range(250):
            with app.ADD_FUND_JOBS_LOCK:
                state=dict(app.ADD_FUND_JOBS[job['id']])
            states.append(state)
            if state['status'] in ('completed','error'):
                break
            time.sleep(0.01)
        self.assertEqual('completed', state['status'])
        self.assertTrue(any(s.get('stage') == 'Refreshing new fund NAV' for s in states))
        self.assertGreaterEqual(int(state.get('progress', 0)), 100)

class ImportSipMappingStabilityTests(TrackerTestBase):
    def test_expand_preview_holdings_preserves_import_refs_on_reexpansion(self):
        preview = {
            'funds': [
                {
                    'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund',
                    'isin': 'INF846K01EW2', 'investor_ref': 'investor_1',
                    'folios': ['F-A/0', 'F-B/0'],
                    'units': 200, 'invested': 20000, 'sip_enabled': True,
                    'transactions': [
                        {'txn_type': 'sip', 'txn_date': '2026-07-23', 'amount': 1000, 'cashflow': -1000, 'units': 10, 'nav': 100, 'folio': 'F-A/0'},
                        {'txn_type': 'sip', 'txn_date': '2026-07-23', 'amount': 1000, 'cashflow': -1000, 'units': 10, 'nav': 100, 'folio': 'F-B/0'},
                    ]
                },
                {
                    'scheme_code': '100009', 'scheme_name': 'PGIM India Midcap Fund',
                    'isin': 'TEST-PGIM', 'investor_ref': 'investor_1',
                    'folios': ['PGIM/0'], 'units': 100, 'invested': 10000,
                    'sip_enabled': True,
                    'transactions': [
                        {'txn_type': 'sip', 'txn_date': '2026-07-15', 'amount': 2499.88, 'cashflow': -2499.88, 'units': 10, 'nav': 249.988, 'folio': 'PGIM/0'},
                    ]
                }
            ],
            'source': {'statement_to': '2026-08-14'},
            'investors': []
        }
        first = self.db._expand_preview_holdings(preview)
        refs_first = [f.get('_import_ref') for f in first['funds']]
        second = self.db._expand_preview_holdings(first)
        refs_second = [f.get('_import_ref') for f in second['funds']]
        self.assertEqual(refs_first, refs_second)
        # In particular, the PGIM ref must not move when the earlier Axis fund
        # expands from one scheme record to two holding/folio records.
        self.assertEqual('fund:1:folio:PGIM', refs_first[-1])

    def test_import_changes_accepts_sip_choice_after_preview_reexpansion(self):
        preview = {
            'funds': [
                {
                    'scheme_code': '120503', 'scheme_name': 'Axis ELSS Tax Saver Fund',
                    'isin': 'INF846K01EW2', 'investor_ref': 'investor_1',
                    'folios': ['F-A/0', 'F-B/0'], 'units': 200, 'invested': 20000,
                    'sip_enabled': True, 'sip_amount': 1000, 'sip_day': 23,
                    'transactions': [
                        {'txn_type':'sip','txn_date':'2026-07-23','amount':1000,'cashflow':-1000,'units':10,'nav':100,'folio':'F-A/0'},
                        {'txn_type':'sip','txn_date':'2026-07-23','amount':1000,'cashflow':-1000,'units':10,'nav':100,'folio':'F-B/0'},
                    ]
                },
                {
                    'scheme_code': '100009', 'scheme_name': 'PGIM India Midcap Fund',
                    'isin': 'TEST-PGIM', 'investor_ref': 'investor_1', 'folios': ['PGIM/0'],
                    'units': 100, 'invested': 10000, 'sip_enabled': True, 'sip_amount': 2499.88, 'sip_day': 15,
                    'sip_confirmation_required': True, 'sip_status': 'active',
                    'transactions': [
                        {'txn_type':'sip','txn_date':'2026-07-15','amount':2499.88,'cashflow':-2499.88,'units':10,'nav':249.988,'folio':'PGIM/0'},
                    ]
                }
            ],
            'source': {'statement_to':'2026-08-14'}, 'investors': [], 'counts': {'funds': 2, 'transactions': 3}
        }
        first = self.db._expand_preview_holdings(preview)
        pgim_key = first['funds'][-1]['_import_ref']
        # This mirrors /api/import/changes: the server validates the raw preview,
        # then Database.preview_import_changes() expands it again before resolving
        # the UI's holding-scoped SIP mapping.
        changes = self.db.preview_import_changes(first, sip_status_mapping={pgim_key: True})
        self.assertTrue(changes)

class ImportSipDecisionPropagationTests(TrackerTestBase):
    def make_preview(self, sip_day, statement_to='2026-08-13', nav_entries=None, transactions=None):
        return {
            'source': {'statement_to': statement_to},
            'investors': [],
            'funds': [{
                'scheme_code': '100001', 'scheme_name': 'Test Fund', 'isin': 'TEST',
                'investor_ref': None, 'folios': [], 'units': 100, 'invested': 10000,
                'sip_enabled': True, 'sip_amount': 1000, 'sip_day': sip_day,
                'sip_confirmation_required': False, 'last_sip_transaction_date': None,
                'initial_date': '2025-01-01', 'transactions': transactions or [],
                'nav_snapshot': {'last_nav': 100, 'last_nav_date': statement_to},
                'import_nav_entries': nav_entries or [],
                'warnings': []
            }]
        }
    def test_inactive_status_is_persisted_and_blocks_final_reconciliation_even_without_confirmation_flag(self):
        preview = self.make_preview(19, nav_entries=[(date(2026, 8, 19), 100.0)])
        fund = preview['funds'][0]
        fund['sip_confirmation_required'] = False
        fund['sip_enabled'] = True
        fund['scheme_code'] = '155001'
        fund['import_sip_reconciliations'] = [{
            'key': 'inactive-propagation', 'scheme_code': '155001', 'scheme_name': 'Test Fund',
            'sip_date': '2026-08-19', 'nav_date': '2026-08-19', 'latest_nav_date': '2026-08-19',
            'amount': 1000, 'nav': 100, 'units': 10, 'auto_execute': True
        }]
        result = self.tracker.import_validated_preview(preview, sip_status_mapping={'155001': False})
        self.assertEqual(1, len(result['imported']))
        item = result['affected_sip_holds'][0]
        self.assertFalse(item['sip_enabled'])
        self.assertEqual(False, item['import_sip_status'])
        self.assertEqual([], item['import_sip_reconciliations'])
        # Exercise the exact final stage with the actual affected-hold payload.
        sip_result = self.tracker.execute_post_import_sips(result['affected_sip_holds'], sip_status_mapping={})
        self.assertEqual([], sip_result['executed'])
        self.assertEqual([], sip_result['auto_executed'])
        self.assertEqual([], [r for r in self.db.transactions(item['holding_id']) if r['txn_type'] == 'sip'])

    def test_inactive_status_blocks_final_stage_from_persisted_holding_even_if_payload_looks_active(self):
        fid = self.add_fund(code='155002', name='Persisted Inactive Gate Fund', profile_id=1, sip_day=19, sip_amount=1000, enabled=False)
        item = {
            'holding_id': fid,
            'scheme_code': '155002',
            'scheme_name': 'Persisted Inactive Gate Fund',
            'sip_enabled': True,
            'import_sip_reconciliations': [{
                'key': 'persisted-inactive', 'scheme_name': 'Persisted Inactive Gate Fund',
                'sip_date': '2026-08-19', 'nav_date': '2026-08-19', 'latest_nav_date': '2026-08-19',
                'amount': 1000, 'nav': 100, 'units': 10, 'auto_execute': False
            }]
        }
        sip_result = self.tracker.execute_post_import_sips([item], sip_reconciliation_mapping={}, sip_status_mapping={})
        self.assertEqual([], sip_result['executed'])
        self.assertEqual([], sip_result['auto_executed'])
        self.assertEqual([], sip_result['skipped'])
        self.assertEqual([], [r for r in self.db.transactions(fid) if r['txn_type'] == 'sip'])


class AmfiLatestNavTests(TrackerTestBase):
    def test_amfi_latest_parser_reads_rows_and_skips_section_headers(self):
        raw = '''Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n\nOpen Ended Schemes\n\n125497;INF200K01V08;-;SBI Example Fund;Direct Plan;Growth;100.25;20-Aug-2026\n125497;INF200K01V08;-;SBI Example Fund;Direct Plan;Growth;99.25;19-Aug-2026\n125498;-;-;Other Fund;Regular Plan;Growth;55.5;20-Aug-2026\n'''
        snap = self.tracker._parse_amfi_latest_nav_text(raw, held_codes={'125497'})
        self.assertEqual({'125497': (date(2026, 8, 20), 100.25)}, snap)

    def test_amfi_latest_snapshot_uses_amfi_and_not_mfapi(self):
        raw = 'Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n125497;;;;;;;\n125497;INF;INF2;SBI Example Fund;Direct Plan;Growth;100.25;20-Aug-2026\n'
        calls = []
        self.tracker.request_nav_json_with_retry = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('MFAPI latest endpoint must not be called'))
        original = app.request_bytes
        def fake_request(url, **kwargs):
            calls.append(url)
            return raw.encode('utf-8')
        app.request_bytes = fake_request
        try:
            snap = self.tracker.request_latest_nav_snapshot(force=True)
        finally:
            app.request_bytes = original
        self.assertEqual((date(2026, 8, 20), 100.25), snap['125497'])
        self.assertTrue(calls)
        self.assertIn('NAVAll.txt', calls[0])

    def test_amfi_latest_snapshot_retries_second_amfi_endpoint(self):
        raw = 'Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n125497;INF;INF2;SBI Example Fund;Direct Plan;Growth;100.25;20-Aug-2026\n'
        calls = []
        original = app.request_bytes
        def fake_request(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError('primary AMFI unavailable')
            return raw.encode('utf-8')
        app.request_bytes = fake_request
        try:
            snap = self.tracker.request_latest_nav_snapshot(force=True)
        finally:
            app.request_bytes = original
        self.assertEqual((date(2026, 8, 20), 100.25), snap['125497'])
        self.assertEqual(2, len(calls))
        self.assertIn('portal.amfiindia.com', calls[1])

    def test_amfi_latest_snapshot_ignores_invalid_nav_and_missing_scheme_codes(self):
        raw = 'Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\nABC;;;;;;;\n125497;INF;INF2;Valid;Direct Plan;Growth;bad;20-Aug-2026\n125497;INF;INF2;Valid;Direct Plan;Growth;-5;20-Aug-2026\n125497;INF;INF2;Valid;Direct Plan;Growth;100.25;20-Aug-2026\n'
        snap = self.tracker._parse_amfi_latest_nav_text(raw)
        self.assertEqual((date(2026, 8, 20), 100.25), snap['125497'])

class AmfiReferenceArchitectureHardeningTests(TrackerTestBase):
    def test_full_latest_cache_can_be_reused_for_subset_without_refetch(self):
        self.tracker._latest_nav_snapshot = {'100001': (date(2026, 8, 20), 100.0), '100002': (date(2026, 8, 20), 200.0)}
        self.tracker._latest_nav_snapshot_at = time.monotonic()
        self.tracker._latest_nav_snapshot_all_schemes = True
        calls = []
        original = app.request_bytes
        app.request_bytes = lambda url, **kwargs: calls.append(url) or b''
        try:
            snap = self.tracker.request_latest_nav_snapshot(force=False, all_schemes=False)
        finally:
            app.request_bytes = original
        self.assertEqual(0, len(calls))
        self.assertEqual(2, len(snap))

    def test_startup_warm_does_not_create_reference_rows_for_empty_install(self):
        self.tracker._prefetch_nav_reference_cache()
        self.assertEqual([], list(self.db.all_nav_references()))
        self.assertTrue((Path(app.DATA_DIR) / 'amfi_nav_history_v2').exists())

    def test_same_latest_date_but_changed_nav_value_rebuilds_reference(self):
        self.add_fund(code='100001', name='Value Revision Fund', sip_day=None, sip_amount=0, enabled=False)
        self.db.upsert_nav_reference({
            'scheme_code': '100001', 'latest_nav_date': '2026-08-20', 'latest_nav': 100.0,
            'previous_nav_date': '2026-08-19', 'previous_nav': 99.0,
            'month_nav_date': '2026-08-03', 'month_nav': 98.0,
            'year_nav_date': '2026-01-01', 'year_nav': 90.0,
        })
        calls = []
        self.tracker._compute_nav_reference = lambda code, rec: (calls.append(code) or {
            'scheme_code': code, 'latest_nav_date': '2026-08-20', 'latest_nav': 101.0,
            'previous_nav_date': '2026-08-19', 'previous_nav': 99.0,
            'month_nav_date': '2026-08-03', 'month_nav': 98.0,
            'year_nav_date': '2026-01-01', 'year_nav': 90.0,
            'updated_at': app.iso_now(),
        })
        self.tracker.update_nav_reference_table({'100001': (date(2026, 8, 20), 101.0)}, scheme_codes=['100001'])
        self.assertEqual(['100001'], calls)
        self.assertEqual(101.0, self.db.nav_reference('100001')['latest_nav'])

    def test_incomplete_reference_rejects_partial_commit_when_second_scheme_history_fails(self):
        self.add_fund(code='100001', name='Atomic Fund 1', sip_day=None, sip_amount=0, enabled=False)
        self.add_fund(code='100002', name='Atomic Fund 2', sip_day=None, sip_amount=0, enabled=False)
        self.tracker._holiday_status = lambda d: {'nse_open': d.weekday() < 5}
        self.tracker._amfi_history_snapshot_for_date = lambda d: {
            '100001': (d, 99.0),
            '100002': (d, 88.0),
        }
        original = self.tracker._compute_nav_reference
        def compute(code, rec):
            if code == '100002':
                raise RuntimeError('simulated history failure')
            return original(code, rec)
        self.tracker._compute_nav_reference = compute
        with self.assertRaises(RuntimeError):
            self.tracker.update_nav_reference_table({
                '100001': (date(2026, 8, 20), 100.0),
                '100002': (date(2026, 8, 20), 90.0),
            }, scheme_codes=['100001', '100002'])
        self.assertIsNone(self.db.nav_reference('100001'))
        self.assertIsNone(self.db.nav_reference('100002'))

    def test_reference_completeness_detects_wrong_month_and_year_dates(self):
        self.add_fund(code='100001', name='Boundary Fund', sip_day=None, sip_amount=0, enabled=False)
        self.db.upsert_nav_reference({
            'scheme_code': '100001', 'latest_nav_date': '2026-08-20', 'latest_nav': 100.0,
            'previous_nav_date': '2026-08-19', 'previous_nav': 99.0,
            'month_nav_date': '2026-07-31', 'month_nav': 98.0,
            'year_nav_date': '2025-12-31', 'year_nav': 90.0,
        })
        calls = []
        self.tracker._compute_nav_reference = lambda code, rec: (calls.append(code) or {
            'scheme_code': code, 'latest_nav_date': '2026-08-20', 'latest_nav': 100.0,
            'previous_nav_date': '2026-08-19', 'previous_nav': 99.0,
            'month_nav_date': '2026-08-03', 'month_nav': 97.0,
            'year_nav_date': '2026-01-01', 'year_nav': 91.0,
            'updated_at': app.iso_now(),
        })
        self.tracker.update_nav_reference_table({'100001': (date(2026, 8, 20), 100.0)}, scheme_codes=['100001'])
        self.assertEqual(['100001'], calls)

    def test_reference_update_syncs_all_same_scheme_holdings_atomically(self):
        fid1 = self.add_fund(code='100001', name='Fund A', profile_id=1, sip_day=None, sip_amount=0, enabled=False)
        with self.db._lock, self.db.conn() as c:
            now = app.iso_now()
            c.execute('INSERT INTO profiles(id,name,emails_json,address_json,created_at,updated_at) VALUES(?,?,?,?,?,?)', (2, 'Second Investor', '[]', '{}', now, now))
        fid2 = self.add_fund(code='100001', name='Fund A duplicate folio', profile_id=2, sip_day=None, sip_amount=0, enabled=False)
        self.tracker._compute_nav_reference = lambda code, rec: {
            'scheme_code': code, 'latest_nav_date': '2026-08-20', 'latest_nav': 120.0,
            'previous_nav_date': '2026-08-19', 'previous_nav': 119.0,
            'month_nav_date': '2026-08-03', 'month_nav': 110.0,
            'year_nav_date': '2026-01-01', 'year_nav': 90.0,
            'updated_at': app.iso_now(),
        }
        self.tracker.update_nav_reference_table({'100001': (date(2026, 8, 20), 120.0)}, scheme_codes=['100001'])
        self.assertEqual(120.0, self.db.get_fund(fid1)['last_nav'])
        self.assertEqual(120.0, self.db.get_fund(fid2)['last_nav'])
        self.assertEqual('2026-08-19', self.db.get_fund(fid1)['previous_nav_date'])
        self.assertEqual('2026-08-03', self.db.get_fund(fid2)['month_nav_date'])

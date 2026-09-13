import sqlite3, tempfile, unittest
from pathlib import Path
import app

class HistoricalGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        db=app.Database(Path(self.tmp.name)/'test.db')
        self.db=db
        with db.conn() as c:
            now=app.iso_now(); c.execute("INSERT INTO profiles(name,emails_json,phone,address_json,pan,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",('P','[]','', '{}','AAAAA1111A',now,now)); pid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute("INSERT INTO schemes(isin,scheme_code,scheme_name,normalized_name,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?)",('INFTEST','999999','Test Fund','test fund',now,now)); sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute("INSERT INTO holdings(profile_id,scheme_id,folio,normalized_folio,scheme_code,scheme_name,isin,units,invested,sip_enabled,sip_amount,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(pid,sid,'F','F','999999','Test Fund','INFTEST',0,0,0,0,now,now)); self.hid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; self.pid=pid
            for d,nav in [('2026-08-01',100),('2026-08-03',105),('2026-08-04',110)]: c.execute("INSERT INTO graph_nav_history(scheme_code,nav_date,nav,fetched_at) VALUES(?,?,?,?)",('999999',d,nav,now))
            tx=[('2026-08-01','sip',1000,-1000,10),('2026-08-03','buy',500,-500,5),('2026-08-04','sell',300,300,-2)]
            for d,t,a,cf,u in tx: c.execute("INSERT INTO transactions(holding_id,txn_type,txn_date,amount,cashflow,units,nav,charges,folio,source,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(self.hid,t,d,a,cf,u,100,0,'F','test','',now))
        self.tr=app.Tracker.__new__(app.Tracker); self.tr.db=db; self.tr.timezone_name='UTC'
    def tearDown(self): self.tmp.cleanup()
    def test_snapshot_uses_existing_transaction_semantics(self):
        self.tr.build_graph_snapshots(self.hid,app.date(2026,8,1),app.date(2026,8,4))
        rows=self.tr.graph_snapshots(self.hid,app.date(2026,8,1),app.date(2026,8,4))
        self.assertEqual(len(rows),3)
        # 1-Aug: 10 units, 1000 invested, value 1000.
        self.assertAlmostEqual(rows[0]['units_held'],10); self.assertAlmostEqual(rows[0]['invested_amount'],1000); self.assertAlmostEqual(rows[0]['value'],1000)
        # 3-Aug: +5 units and +500 invested.
        self.assertAlmostEqual(rows[1]['units_held'],15); self.assertAlmostEqual(rows[1]['invested_amount'],1500); self.assertAlmostEqual(rows[1]['value'],1575)
        # 4-Aug: 2/15 of the holding cost is redeemed pro-rata.
        self.assertAlmostEqual(rows[2]['units_held'],13); self.assertAlmostEqual(rows[2]['invested_amount'],1300); self.assertAlmostEqual(rows[2]['value'],1430)
    def test_snapshot_invalidation_on_transaction_change(self):
        self.tr.build_graph_snapshots(self.hid,app.date(2026,8,1),app.date(2026,8,4))
        self.tr.invalidate_graph_snapshots(self.hid,'2026-08-03')
        self.assertEqual(self.tr.graph_snapshots(self.hid,app.date(2026,8,3),app.date(2026,8,4)),[])
    def test_generate_graph_dataset_from_cached_history(self):
        self.tr.local_today=lambda: app.date(2026,8,4)
        data=self.tr.generate_graph_dataset(self.pid,[self.hid],['total_value','profit_pct'],'1m')
        self.assertEqual(len(data['series']),2)
        self.assertFalse(data['warnings'])
        values=[s for s in data['series'] if s['parameter']=='total_value'][0]['points']
        self.assertGreaterEqual(len(values),3)
        self.assertEqual(data.get('holding_count'),1)
        self.assertIn('total_value',data.get('statistics',{}))
        stats=data['statistics']['total_value']
        self.assertAlmostEqual(stats['max'],1575.0)
        self.assertAlmostEqual(stats['min'],1000.0)
        self.assertAlmostEqual(stats['median'],1430.0)
        self.assertEqual(stats['max_date'],'2026-08-03')
        self.assertEqual(stats['min_date'],'2026-08-01')

    def test_stats_are_not_returned_as_single_fund_stats_for_multiple_holdings(self):
        # Duplicate the holding under a second fund with its own NAV/transactions.
        with self.db.conn() as c:
            row=c.execute('SELECT profile_id,scheme_id,folio,normalized_folio,scheme_code,scheme_name,isin,created_at,updated_at FROM holdings WHERE id=?',(self.hid,)).fetchone()
            c.execute("INSERT INTO schemes(isin,scheme_code,scheme_name,normalized_name,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?)",('INFTEST2','999998','Test Fund 2','test fund 2',row['created_at'],row['updated_at']))
            sid2=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute("INSERT INTO holdings(profile_id,scheme_id,folio,normalized_folio,scheme_code,scheme_name,isin,units,invested,sip_enabled,sip_amount,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(row['profile_id'],sid2,'G','G','999998',row['scheme_name']+' 2','INFTEST2',0,0,0,0,row['created_at'],row['updated_at']))
            hid2=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for d,nav in [('2026-08-01',50),('2026-08-03',55),('2026-08-04',60)]: c.execute("INSERT INTO graph_nav_history(scheme_code,nav_date,nav,fetched_at) VALUES(?,?,?,?)",('999998',d,nav,row['created_at']))
            c.execute("INSERT INTO transactions(holding_id,txn_type,txn_date,amount,cashflow,units,nav,charges,folio,source,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(hid2,'sip','2026-08-01',500,-500,10,50,0,'G','test','',row['created_at']))
        self.tr.local_today=lambda: app.date(2026,8,4)
        data=self.tr.generate_graph_dataset(self.pid,[self.hid,hid2],['total_value'],'1m')
        self.assertEqual(data.get('holding_count'),2)
        self.assertEqual(data.get('statistics'),{})

    def test_lttb_reduces_long_series(self):
        pts=[(i,float(i*i%101)) for i in range(3000)]
        out=self.tr._lttb(pts,1400)
        self.assertLessEqual(len(out),1400); self.assertEqual(out[0],pts[0]); self.assertEqual(out[-1],pts[-1])

    def test_fetch_graph_nav_range_uses_database_lock(self):
        payload={
            'status':'SUCCESS',
            'data':[
                {'date':'04-08-2026','nav':'110.0'},
                {'date':'03-08-2026','nav':'105.0'},
            ],
        }
        self.tr.request_nav_json_with_retry=lambda url: payload
        self.assertFalse(hasattr(self.tr, '_lock'))
        count=self.tr._fetch_graph_nav_range('999999', app.date(2026,8,3), app.date(2026,8,4))
        self.assertEqual(count, 2)
        rows=self.tr.graph_nav_entries('999999', app.date(2026,8,3), app.date(2026,8,4))
        self.assertEqual(rows, [(app.date(2026,8,3),105.0),(app.date(2026,8,4),110.0)])

if __name__=='__main__': unittest.main()

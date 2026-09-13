import unittest
from datetime import date
import app

class DB:
    def __init__(self, funds, tx=None, sip_exec=None, live_exec=None):
        self.funds=funds; self.tx=tx or {}; self.sip_exec=sip_exec or []; self.live_exec=live_exec or []
    def all_funds(self, profile_id=None): return self.funds
    def transactions(self,hid): return self.tx.get(hid,[])
    def sip_transactions(self, profile_id=None):
        out=[]
        for rows in self.tx.values(): out += rows
        return out
    def sip_executions(self, profile_id=None): return self.sip_exec
    def live_sip_executions(self, profile_id=None, execution_date=None): return self.live_exec
    def live_sip_execution_for_cycle(self,*a): return None
    def sip_execution_for_cycle(self,*a): return None
    def live_sip_transaction_for_cycle(self, holding_id, sip_date):
        for row in self.tx.get(int(holding_id), []):
            if row.get('txn_type') == 'sip' and str(row.get('note') or '').startswith(f'Scheduled SIP for {sip_date}'):
                return row
        return None

class Stress(unittest.TestCase):
    def mk(self, today=date(2026,8,20), dates=None):
        t=object.__new__(app.Tracker); t.local_today=lambda:today
        ds=dates or {}
        t._holiday_calendar_payload=lambda:{'dates':ds}
        return t
    def fund(self,hid,name,day,nav='2026-08-20',last='2026-07'):
        return {'id':hid,'scheme_name':name,'sip_enabled':1,'sip_amount':1000,'sip_day':day,'initial_date':'2026-01-01','last_nav_date':nav,'last_sip_cycle':last}
    def test_same_day_many_funds_group_order(self):
        t=self.mk(dates={'2026-08-19':{'nse_open':True},'2026-08-20':{'nse_open':True}})
        f=[self.fund(3,'C',19),self.fund(1,'A',19),self.fund(2,'B',19)]
        t.db=DB(f); s=t._build_sip_status(1)
        self.assertEqual([x['fund_name'] for x in s['sip_date_fund_groups'][0]['funds']],['A','B','C'])
    def test_delayed_due_is_in_upcoming_and_next(self):
        t=self.mk(dates={'2026-08-19':{'nse_open':True},'2026-08-20':{'nse_open':True}})
        f=[self.fund(1,'Delayed',19,'2026-08-18')]
        t.db=DB(f); s=t._build_sip_status(1)
        self.assertEqual(s['next_expected_sip_funds'],['Delayed'])
        self.assertEqual(s['upcoming_sip_funds'],['Delayed'])
        self.assertEqual(s['next_expected_sip_date'],'2026-08-20')
    def test_executed_current_not_upcoming(self):
        t=self.mk(dates={'2026-08-19':{'nse_open':True},'2026-08-20':{'nse_open':True},'2026-09-21':{'nse_open':True},'2026-09-19':{'nse_open':False},'2026-09-20':{'nse_open':False}})
        f=[self.fund(1,'Done',19,'2026-08-20','2026-07')]
        tx={1:[{'holding_id':1,'txn_type':'sip','txn_date':'2026-08-19','amount':1000,'units':10,'note':'Scheduled SIP for 2026-08-19 (NAV date 2026-08-19)'}]}
        t.db=DB(f,tx); s=t._build_sip_status(1)
        self.assertEqual(s['executed_sip_funds'],['Done'])
        self.assertEqual(s['upcoming_sip_funds'],[])
        self.assertEqual(s['sip_date_fund_groups'][0]['cycle_date'],'2026-09-19')
    def test_last_cycle_inconsistent_with_missing_tx_reveals_risk(self):
        t=self.mk(dates={'2026-08-19':{'nse_open':True},'2026-09-19':{'nse_open':True}})
        f=[self.fund(1,'Corrupt',19,'2026-08-20','2026-08')]
        t.db=DB(f)
        s=t._build_sip_status(1)
        self.assertEqual(s['sip_date_fund_groups'][0]['cycle_date'],'2026-09-19')
    def test_future_expected_open_date(self):
        t=self.mk(today=date(2026,8,20), dates={'2026-08-21':{'nse_open':True},'2026-08-22':{'nse_open':False},'2026-08-23':{'nse_open':False},'2026-08-24':{'nse_open':True}})
        f=[self.fund(1,'Future',21,'2026-08-20','2026-07')]
        t.db=DB(f); s=t._build_sip_status(1)
        self.assertEqual(s['next_expected_sip_date'],'2026-08-22')
    def test_multiple_groups_sorted_by_full_date(self):
        t=self.mk(dates={'2026-08-02':{'nse_open':True},'2026-08-10':{'nse_open':True},'2026-08-20':{'nse_open':True},'2026-09-02':{'nse_open':True}})
        f=[self.fund(1,'Sep2',2,'2026-08-20','2026-08'),self.fund(2,'Aug10',10,'2026-08-20','2026-07')]
        t.db=DB(f); s=t._build_sip_status(1)
        self.assertEqual([g['cycle_date'] for g in s['sip_date_fund_groups']],['2026-08-10','2026-09-02'])
    def test_month_day_clamp(self):
        t=self.mk(today=date(2026,2,20), dates={'2026-02-28':{'nse_open':True},'2026-02-27':{'nse_open':True},'2026-03-31':{'nse_open':True},'2026-04-30':{'nse_open':True}})
        f=[self.fund(1,'Day31',31,'2026-02-20','2026-01')]
        t.db=DB(f); s=t._build_sip_status(1)
        self.assertEqual(s['sip_date_fund_groups'][0]['cycle_date'],'2026-02-28')
    def test_profile_filter_is_respected_by_builder_input(self):
        t=self.mk(dates={'2026-08-19':{'nse_open':True},'2026-08-20':{'nse_open':True}})
        f=[self.fund(1,'One',19),self.fund(2,'Two',20)]
        t.db=DB(f); s=t._build_sip_status(999)
        self.assertEqual(len(s['sip_date_fund_groups']),2)

if __name__=='__main__': unittest.main()

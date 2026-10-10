import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from crs.db import set_setting
from crs.errors import *
from crs.services.billing import BillingWorker


def toggle(s,key,value):
    with s.db.transaction() as conn: set_setting(conn,key,value)
    s.catalog.invalidate()

def test_close_during_registration_rejected(s):
    with pytest.raises(RegistrationInProgressError): s.closing.close_registration()
    assert s.semesters.current().code=='2026F'


def test_close_level_bill_retry(s):
    toggle(s,'billing_available','0')
    s.semesters.end_add_drop('2026F')
    report=s.closing.close_registration()
    assert report.bills==13
    assert report.billing_pending==13
    assert report.cancelled['CS150-01']=='无教授授课'
    assert report.closed and report.leveled
    with s.db.reader() as conn:
        for row in conn.execute("SELECT offering_id FROM offering_state WHERE semester='2026F' AND status='CLOSED'"):
            n=conn.execute("SELECT count(*) FROM schedule_item WHERE offering_id=? AND status='COMMITTED'",(row[0],)).fetchone()[0]
            assert 3<=n<=10
        assert conn.execute("SELECT count(*) FROM schedule_item si JOIN schedule sc ON sc.id=si.schedule_id WHERE sc.student_id=17 AND si.status='COMMITTED'").fetchone()[0]==0
    bills=s.billing.list_outbox('2026F')
    for b in bills:
        payload=json.loads(b['payload'])
        assert b['amount']==sum(c['credits'] for c in payload['final_schedule'])*600
    toggle(s,'billing_available','1')
    assert s.billing.dispatch_due(force=True)==(13,0)
    total=len(s.billing_gateway.invoices())
    assert s.billing.dispatch_due(force=True)==(0,0)
    # 模拟接收后确认丢失，再发送同一幂等键。
    b=bills[0]
    s.billing_gateway.send(b['idempotency_key'],b['student_id'],b['semester'],b['amount'],b['payload'],'2026-10-10')
    assert len(s.billing_gateway.invoices())==total
    with pytest.raises(ConflictError): s.closing.close_registration()
    s.semesters.open_semester('2027S','2027-03-01 12:00')
    assert s.semesters.current().code=='2027S'


def test_close_rolls_back_internal_failure(s,monkeypatch):
    s.semesters.end_add_drop('2026F')
    def fail(*args): raise RuntimeError('故障注入：账单写入失败')
    monkeypatch.setattr(s.closing,'_create_bills',fail)
    with pytest.raises(RuntimeError): s.closing.close_registration()
    assert s.semesters.current().code=='2026F'
    assert not s.billing.list_outbox('2026F')
    with s.db.reader() as c:
        assert c.execute("SELECT count(*) FROM offering_state WHERE semester='2026F' AND status<>'OPEN'").fetchone()[0]==0


def test_catalog_outage_cache_and_singleflight(s):
    s.catalog.invalidate()
    before=s.catalog_client.fetch_count
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _:s.catalog.snapshot('2026F'),range(24)))
    assert s.catalog_client.fetch_count==before+1
    with s.db.transaction() as c: set_setting(c,'catalog_available','0')
    assert s.catalog.snapshot('2026F').semester=='2026F' # TTL 内复用已有缓存
    s.catalog.invalidate()
    with pytest.raises(CatalogUnavailableError): s.registration.available_offerings()
    s.semesters.end_add_drop('2026F')
    with pytest.raises(CatalogUnavailableError): s.closing.close_registration()
    assert s.semesters.current().code=='2026F'
    assert s.teaching.report_card(1).catalog_degraded


def test_legacy_read_only(s):
    conn=sqlite3.connect(f'file:{s.catalog_client.path}?mode=ro',uri=True)
    with pytest.raises(sqlite3.OperationalError): conn.execute('DELETE FROM course')
    conn.close()


def test_backoff_and_worker(s):
    assert [s.billing.backoff_seconds(x) for x in [1,2,3,100000]]==[10,20,40,300]
    worker=BillingWorker(s.billing,0.01)
    worker.start(); worker.stop(); worker.join(timeout=1)
    assert not worker.is_alive()


def test_postcommit_delivery_error_keeps_success(s,monkeypatch):
    s.semesters.end_add_drop('2026F')
    def fail(): raise OSError('故障注入：投递连接异常')
    monkeypatch.setattr(s.billing,'dispatch_due',fail)
    assert s.closing.close_registration().bills==13
    assert s.semesters.current() is None
    assert s.billing.pending_count()==13

from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from crs.errors import *


def save(s,sid,p,a=(),version=None,submit=True):
    return s.registration.save_schedule(sid,p,a,version,submit)[0]

def test_draft_submit_delete(s):
    draft=save(s,18,[201],[213],submit=False)
    assert draft.status=='DRAFT'
    before=s.registration.seat_status()[201]['enrolled']
    registered=save(s,18,[201],[213],draft.version)
    assert registered.status=='SUBMITTED'
    assert s.registration.seat_status()[201]['enrolled']==before+1
    s.registration.delete_schedule(18,registered.version)
    assert s.registration.get_schedule(18) is None
    assert s.registration.seat_status()[201]['enrolled']==before

@pytest.mark.parametrize('p,a', [([201,201],[]),([202,203],[]),([999],[]),([201,204,210,211,213],[]),([201],[210,211,213]),([],[])])
def test_invalid_selection(s,p,a):
    with pytest.raises(ValidationError): save(s,18,p,a)
    assert s.registration.get_schedule(18) is None

def test_prerequisite_conflict_and_rollback(s):
    original=save(s,18,[201])
    with pytest.raises(ScheduleRejectedError): save(s,18,[205],version=original.version)
    assert s.registration.get_schedule(18).items[0].offering.offering_id==201
    # s1 已通过 CS101；数据结构与线性代数时间重合。
    with pytest.raises(ScheduleRejectedError,match='课表未能提交'):
        save(s,1,[202,211])
    assert s.registration.get_schedule(1) is None

def test_optimistic_lock(s):
    draft=save(s,18,[201],submit=False)
    updated=save(s,18,[210],version=draft.version,submit=False)
    with pytest.raises(ConflictError): save(s,18,[211],version=draft.version)
    with pytest.raises(ConflictError): s.registration.delete_schedule(18,draft.version)
    assert s.registration.get_schedule(18).version==updated.version

def test_save_releases_and_preserves_seats(s):
    first=save(s,18,[201,210])
    second=save(s,18,[201],[210],first.version,False)
    assert second.items[0].status=='ENROLLED'
    assert second.alternates[0].status=='SELECTED'


def test_last_seat_concurrency(s):
    # 211 已有 9 人，两个独立连接同时争抢最后一个名额。
    barrier=threading.Barrier(2)
    def attempt(sid):
        barrier.wait()
        try:
            save(s,sid,[211])
            return 'accepted'
        except ScheduleRejectedError:
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(attempt,[18,19]))
    assert sorted(outcomes)==['accepted','rejected']
    assert s.registration.seat_status()[211]['enrolled']==10


def test_deadline_and_inactive(s):
    st=s.people.get_student(18)
    data={**st.__dict__,'status':'SUSPENDED'}
    s.people.update_student(18,data)
    with pytest.raises(PermissionDeniedError): save(s,18,[201])
    s.semesters.end_add_drop('2026F')
    with pytest.raises(RegistrationClosedError): save(s,19,[201])

def test_missing_schedule(s):
    with pytest.raises(NotFoundError): s.registration.delete_schedule(18,0)

def test_exact_four_plus_two(s):
    sc=save(s,1,[201,210,211,204],[213,207])
    assert len(sc.primaries)==4 and len(sc.alternates)==2
    assert sum(i.status=='ENROLLED' for i in sc.items)==4

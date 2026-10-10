import pytest
from crs.errors import *


def student_form(**changes):
    return dict(name='测试学生',birth_date='2005-02-01',ssn='900-10-0001',status='ENROLLED',graduation_date='2029-07-01',**changes)

def test_people_crud(s):
    person,password=s.people.add_student(student_form())
    assert password=='20050201'
    assert s.auth.authenticate(person.username,password).student_id==person.id
    person=s.people.update_student(person.id,{**student_form(),'name':'更新姓名'})
    assert person.name=='更新姓名'
    assert len(s.people.list_students('更新姓名'))==1
    s.people.delete_student(person.id)
    with pytest.raises(NotFoundError): s.people.get_student(person.id)
    prof,_=s.people.add_professor(dict(name='新教授',birth_date='1980-02-01',ssn='901-10-0001',status='ACTIVE',department='CS'))
    s.people.update_professor(prof.id,{**prof.__dict__,'name':'改名教授'})
    s.people.delete_professor(prof.id)
    with pytest.raises(NotFoundError): s.people.get_professor(prof.id)

@pytest.mark.parametrize('change',[{'ssn':'bad'},{'name':''},{'birth_date':'tomorrow'},{'status':'X'},{'graduation_date':'2000-01-01'}])
def test_person_validation(s,change):
    with pytest.raises(ValidationError): s.people.add_student({**student_form(),**change})

def test_duplicate_history_notfound(s):
    s.people.add_student(student_form())
    with pytest.raises(ConflictError): s.people.add_student(student_form())
    with pytest.raises(ConflictError): s.people.delete_student(1)
    with pytest.raises(ConflictError): s.people.delete_professor(1)
    with pytest.raises(NotFoundError): s.people.get_student('wrong')

def test_teaching_permissions_conflict(s):
    # 教授1已有201、205，新增204。
    result=s.teaching.select_courses_to_teach(1,[201,205,204])
    assert 'CS150-01' in result['added']
    with pytest.raises(ConflictError): s.teaching.select_courses_to_teach(2,[204])
    with pytest.raises(PermissionDeniedError): s.teaching.select_courses_to_teach(1,[210])
    with pytest.raises(PermissionDeniedError): s.teaching.roster(2,201)
    # 教授1申请两个相交时段：CS201(205) 与同院系？使用释放后的自定义时间重合目录。
    snapshot=s.catalog.snapshot('2026F')
    from dataclasses import replace
    snapshot.offerings[204]=replace(snapshot.offerings[204],slot=snapshot.offerings[201].slot)
    with pytest.raises(ConflictError): s.teaching.select_courses_to_teach(1,[201,204])
    with s.db.reader() as c:
        assert c.execute('SELECT professor_id FROM offering_state WHERE offering_id=205').fetchone()[0]==1

def test_grades_report(s):
    roster=s.teaching.roster(1,101)
    sid=roster[0].student_id
    assert s.teaching.submit_grades(1,101,{sid:'A'}) in (0,1)
    assert s.teaching.submit_grades(1,101,{sid:''})==0
    assert s.teaching.submit_grades(1,101,{sid:'B'})==1
    with pytest.raises(ValidationError): s.teaching.submit_grades(1,101,{sid:'E'})
    with pytest.raises(ValidationError): s.teaching.submit_grades(1,101,{20:'A'})
    with pytest.raises(PermissionDeniedError): s.teaching.submit_grades(2,101,{sid:'A'})
    with pytest.raises(PermissionDeniedError): s.teaching.submit_grades(1,201,{13:'A'})
    assert s.teaching.report_card(sid).lines
    with pytest.raises(NotFoundError): s.teaching.report_card(20)

def test_login_lock_and_change(env):
    app,s,clock=env
    for _ in range(5):
        with pytest.raises(AuthenticationError): s.auth.authenticate('s1','wrong')
    with pytest.raises(AuthenticationError): s.auth.authenticate('s1','123456')
    clock.advance(minutes=6)
    account=s.auth.authenticate('s1','123456')
    s.auth.change_password(account.id,'123456','abcdef','abcdef')
    assert s.auth.authenticate('s1','abcdef').id==account.id
    with pytest.raises(ValidationError): s.auth.change_password(account.id,'wrong','a','b')

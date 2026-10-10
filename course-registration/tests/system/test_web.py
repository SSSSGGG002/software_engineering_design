import pytest
from conftest import login,post

@pytest.mark.parametrize('name,urls',[
    ('admin',['/registrar/','/registrar/students/','/registrar/professors/','/registrar/students/new','/registrar/students/1/edit','/registrar/professors/1/edit','/registrar/students/1/delete','/admin/']),
    ('s1',['/student/','/student/schedule','/student/report','/student/api/seats','/password']),
    ('p1',['/professor/','/professor/teach','/professor/roster/101','/professor/grades'])])
def test_all_role_pages(client,name,urls):
    assert login(client,name,'admin123' if name=='admin' else '123456').status_code==302
    for url in urls:
        response=client.get(url)
        assert response.status_code==200,(url,response.text)


def test_csrf_and_role_boundaries(client):
    login(client)
    assert client.get('/registrar/').status_code==403
    assert client.get('/professor/grades').status_code==403
    response=client.post('/student/schedule',data={'primary':'201','action':'submit','version':'0'})
    assert response.status_code==302
    assert client.get('/absent').status_code==404
    assert client.get('/logout').status_code==405


def test_schedule_post_and_identity(client,env):
    login(client,'s18')
    client.get('/student/schedule')
    response=post(client,'/student/schedule',{'primary':['201','210'],'alternate':['213'],'action':'submit','version':'0','student_id':'19'})
    assert response.status_code==302
    s=env[1]
    assert s.registration.get_schedule(18)
    assert s.registration.get_schedule(19) is None
    response=client.get('/student/schedule/delete')
    assert response.status_code==200
    version=s.registration.get_schedule(18).version
    assert post(client,'/student/schedule/delete',{'confirm':'no','version':str(version)}).status_code==302
    assert s.registration.get_schedule(18)
    post(client,'/student/schedule/delete',{'confirm':'yes','version':str(version)})
    assert s.registration.get_schedule(18) is None


def test_report_reauth_no_store_and_owner(client):
    login(client)
    assert '确认身份' in client.get('/student/report?student_id=20').text
    assert post(client,'/student/report',{'password':'123456'}).status_code==302
    response=client.get('/student/report?student_id=20')
    assert '个人成绩单' in response.text and '林晓' in response.text
    assert response.headers['Cache-Control']=='no-store'


def test_registrar_crud_and_close_web(client):
    login(client,'admin','admin123')
    client.get('/registrar/students/new')
    response=post(client,'/registrar/students/new',dict(name='页面新增',birth_date='2005-01-01',ssn='888-99-0001',status='ENROLLED',graduation_date='2029-07-01'))
    assert response.status_code==302
    assert '页面新增' in client.get('/registrar/students/').text
    post(client,'/admin/end',{'semester':'2026F','confirm':'yes'})
    response=post(client,'/admin/close',{'confirm':'yes'})
    assert response.status_code==200 and '注册已关闭' in response.text


def test_login_bad_and_redirect(client):
    assert login(client,'s1','bad').status_code==401
    # 表单 next 指向外部也只能回到本系统。
    with client.session_transaction() as sess: token=sess['_csrf']
    r=client.post('/login?next=https://example.com',data={'username':'s1','password':'123456','_csrf':token})
    assert r.location=='/student/'

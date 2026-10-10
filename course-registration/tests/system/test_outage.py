from conftest import login
from crs.db import set_setting


def test_professor_catalog_outage_does_not_redirect_loop(client,env):
    login(client,'p1')
    s=env[1]
    with s.db.transaction() as c: set_setting(c,'catalog_available','0')
    s.catalog.invalidate()
    response=client.get('/professor/',follow_redirects=True)
    assert response.status_code==503
    assert '暂时无法访问' in response.text
    assert len(response.history)==0


def test_api_failure_is_json(client,env):
    login(client)
    s=env[1]
    with s.db.transaction() as c: set_setting(c,'catalog_available','0')
    s.catalog.invalidate()
    response=client.get('/student/api/seats',headers={'Accept':'application/json'})
    assert response.status_code==503 and response.is_json

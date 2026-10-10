"""每个测试独立临时数据库，不污染演示数据。"""
from datetime import datetime
import pytest
from crs import create_app
from crs.clock import FakeClock
from crs.config import TestConfig
from crs.seed import seed_demo

@pytest.fixture
def env(tmp_path):
    config = {k: getattr(TestConfig,k) for k in dir(TestConfig) if k.isupper()}
    config.update(DATABASE=str(tmp_path/'app.db'), CATALOG_DATABASE=str(tmp_path/'catalog.db'),
                  BILLING_DATABASE=str(tmp_path/'billing.db'))
    clock = FakeClock(datetime(2026, 10, 10, 9))
    app = create_app(config, clock=clock)
    seed_demo(app, real_clock=clock)
    app.extensions['crs'].catalog.invalidate()
    return app, app.extensions['crs'], clock

@pytest.fixture
def s(env):
    return env[1]

@pytest.fixture
def client(env):
    return env[0].test_client()

def login(client, name='s1', password='123456'):
    import re
    page = client.get('/login')
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)
    return client.post('/login', data={'username':name,'password':password,'_csrf':token})

def post(client, url, data=None):
    with client.session_transaction() as session:
        token = session.get('_csrf')
    if not token:
        client.get('/')
        client.get('/password')
        with client.session_transaction() as session:
            token = session['_csrf']
    return client.post(url, data={**(data or {}), '_csrf':token})

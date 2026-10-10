"""本地教学演示入口；默认仅监听本机。"""
import os
import secrets
from pathlib import Path

from waitress import serve
from crs import create_app

if __name__ == '__main__':
    # 随安装生成本地密钥；请勿把 instance 目录提交到版本库。
    path = Path(__file__).resolve().parent / 'instance'
    path.mkdir(exist_ok=True)
    secret = path / 'secret.key'
    if not secret.exists():
        secret.write_text(secrets.token_hex(32), encoding='utf-8')
    os.environ.setdefault('CRS_SECRET_KEY', secret.read_text(encoding='utf-8').strip())
    app = create_app({'SECRET_KEY': os.environ['CRS_SECRET_KEY']})
    with app.extensions['crs'].db.reader() as conn:
        count = conn.execute('SELECT count(*) FROM user_account').fetchone()[0]
    if not count:
        raise SystemExit('请先运行：python -m flask --app crs init-demo')
    print('Wylie 课程注册系统：http://127.0.0.1:5000（Ctrl+C 退出）', flush=True)
    serve(app, host='127.0.0.1', port=5000, threads=8)

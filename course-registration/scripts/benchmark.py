"""本地小规模性能抽样，不代表题目 2000/500 用户容量验收。
执行：python scripts/benchmark.py --output results/benchmark.json
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from crs import create_app
from crs.clock import FakeClock
from crs.config import TestConfig
from crs.seed import seed_demo


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='results/benchmark.json')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory() as d:
        cfg={k:getattr(TestConfig,k) for k in dir(TestConfig) if k.isupper()}
        cfg.update(DATABASE=f'{d}/app.db',CATALOG_DATABASE=f'{d}/catalog.db',BILLING_DATABASE=f'{d}/billing.db')
        clock=FakeClock(datetime(2026,10,10,9))
        app=create_app(cfg,clock=clock)
        seed_demo(app,clock)
        s=app.extensions['crs']
        s.catalog_client.latency_seconds=0.8
        s.catalog.invalidate()
        start=time.perf_counter();s.catalog.snapshot('2026F');cold=time.perf_counter()-start
        def read(_):
            start=time.perf_counter()
            s.registration.seat_status()
            return (time.perf_counter()-start)*1000
        start=time.perf_counter()
        with ThreadPoolExecutor(max_workers=20) as pool:
            values=list(pool.map(read,range(200)))
        duration=time.perf_counter()-start
        values.sort()
        result={'time':datetime.now().isoformat(timespec='seconds'),'python':platform.python_version(),
                'mode':'进程内服务调用；20 个线程；200 次名额查询；不含 HTTP/浏览器/真实遗留系统',
                'cold_catalog_ms':round(cold*1000,2),'warm_median_ms':round(statistics.median(values),2),
                'warm_p95_ms':round(values[189],2),'warm_max_ms':round(max(values),2),
                'elapsed_seconds':round(duration,3),'successful_queries':len(values),
                'legacy_fetch_count':s.catalog_client.fetch_count,
                'limitations':'未进行2000/500用户并发验收、7x24可用性或真实Ingres延迟测试'}
        path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

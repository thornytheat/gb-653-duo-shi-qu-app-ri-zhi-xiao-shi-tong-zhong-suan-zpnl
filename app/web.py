from pathlib import Path
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
PAGES = [('/logs', 'm1_logs.html', '日志采信'), ('/time', 'm2_time.html', '时间解析'), ('/events', 'm3_events.html', '事件去重'), ('/buckets', 'm4_buckets.html', '小时分桶'), ('/watermark', 'm5_watermark.html', '迟到修订'), ('/runs', 'm6_runs.html', '批次对账')]

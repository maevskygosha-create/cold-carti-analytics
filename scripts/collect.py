
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from html import unescape

URL = "https://t.me/coldcarti"
FILE = Path("metrics.json")

request = Request(
    URL,
    headers={"User-Agent": "Mozilla/5.0"}
)

with urlopen(request, timeout=30) as response:
    html = response.read().decode("utf-8")

match = re.search(
    r'([\d\s,.]+)\s+subscribers',
    unescape(html),
    re.IGNORECASE
)

if not match:
    raise RuntimeError("Не удалось получить подписчиков Telegram")

raw = match.group(1)
followers = int(re.sub(r"\D", "", raw))

today = datetime.now(timezone.utc).date().isoformat()

if FILE.exists():
    data = json.loads(FILE.read_text(encoding="utf-8"))
else:
    data = {"snapshots": []}

snapshots = data.setdefault("snapshots", [])

snapshots[:] = [
    row for row in snapshots
    if not (
        row.get("date") == today
        and row.get("platform") == "telegram"
    )
]

snapshots.append({
    "date": today,
    "platform": "telegram",
    "followers": followers,
    "status": "ok",
    "source": URL
})

FILE.write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print(f"Telegram: {followers} подписчиков")

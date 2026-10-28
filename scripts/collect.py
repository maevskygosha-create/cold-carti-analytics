
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from html import unescape

FILE = Path("metrics.json")
TODAY = datetime.now(timezone.utc).date().isoformat()


def get_page(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"
        }
    )
    with urlopen(request, timeout=30) as response:
        return unescape(
            response.read().decode("utf-8", errors="replace")
        )


def collect_telegram():
    page = get_page("https://t.me/coldcarti")

    match = re.search(
        r"([\d\s,.]+)\s+subscribers",
        page,
        re.IGNORECASE
    )

    if not match:
        raise ValueError("Telegram: число не найдено")

    followers = int(re.sub(r"\D", "", match.group(1)))

    if followers <= 0:
        raise ValueError("Telegram: некорректное число")

    return {
        "date": TODAY,
        "platform": "telegram",
        "followers": followers,
        "status": "ok",
        "source": "https://t.me/coldcarti"
    }


def collect_vk():
    page = get_page("https://m.vk.com/themostcold")

    # Убираем HTML-теги и лишние пробелы.
    text = re.sub(r"<[^>]+>", " ", page)
    text = re.sub(r"\s+", " ", text)

    # Ищем число непосредственно перед словом
    # "подписчиков" или "участников".
    match = re.search(
        r"(\d[\d\s]*[,.]?\d*)\s*"
        r"(тыс\.?|тысяч|k|к)?\s*"
        r"(?:подписчиков|участников)",
        text,
        re.IGNORECASE
    )

    if not match:
        raise ValueError("VK: число подписчиков не найдено")

    raw = match.group(1).replace(" ", "").replace(",", ".")
    unit = (match.group(2) or "").lower()

    if unit:
        followers = round(float(raw) * 1000)
        approximate = True
    else:
        followers = int(raw.replace(".", ""))
        approximate = False

    if not 1000 <= followers <= 5000000:
        raise ValueError("VK: подозрительное значение")

    return {
        "date": TODAY,
        "platform": "vk",
        "followers": followers,
        "approximate": approximate,
        "status": "ok",
        "source": "https://m.vk.com/themostcold"
    }


if FILE.exists():
    data = json.loads(FILE.read_text(encoding="utf-8"))
else:
    data = {"snapshots": []}

snapshots = data.setdefault("snapshots", [])

for collector in [collect_telegram, collect_vk]:
    try:
        result = collector()

        snapshots[:] = [
            row for row in snapshots
            if not (
                row.get("date") == TODAY
                and row.get("platform") == result["platform"]
            )
        ]

        snapshots.append(result)

        print(
            result["platform"],
            result["followers"],
            "OK"
        )

    except Exception as error:
        print("Ошибка сбора:", error)

FILE.write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print("Сбор завершён")

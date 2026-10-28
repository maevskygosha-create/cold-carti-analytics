#!/usr/bin/env python3
"""Public best-effort collectors. No passwords, bypasses, or fake metrics."""
import json
import os
import re
import html
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

DATA = Path("metrics.json")
STATUS = Path("status.json")
SOURCES = {
    "telegram": "https://t.me/coldcarti",
    "yandex": "https://music.yandex.ru/artist/6487554",
    "instagram": "https://www.instagram.com/realcoldcarti/",
    "tiktok": "https://www.tiktok.com/@coldcarti",
}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def fetch(url, headers=None):
    hs = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}
    hs.update(headers or {})
    req = Request(url, headers=hs)
    with urlopen(req, timeout=25) as resp:
        if resp.status != 200: raise ValueError("HTTP " + str(resp.status))
        return resp.read(4_000_000).decode("utf-8", errors="replace")

def integer(v):
    if isinstance(v, bool): return None
    if isinstance(v, (int,float)) and 0 <= v < 10**12: return int(v)
    if not isinstance(v,str): return None
    raw=v.strip().replace("\u00a0","").replace(" ","").replace(",",".")
    m=re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kKmMкК]|тыс\.?|млн\.?)?",raw,re.I)
    if not m: return None
    suffix=(m.group(2) or "").lower()
    factor=1_000_000 if suffix in ("m","м","млн","млн.") else (1000 if suffix in ("k","к","тыс","тыс.") else 1)
    return round(float(m.group(1))*factor)

def collect_telegram():
    content=html.unescape(fetch(SOURCES["telegram"]))
    m=re.search(r'([\d][\d\s\u00a0,\.]*)\s+subscribers\b', content, re.I)
    if not m: raise ValueError("Не найдена публичная численность подписчиков")
    n=integer(m.group(1).strip())
    if n is None or n<1: raise ValueError("Не удалось распознать число")
    return {"followers":n}

def collect_tiktok():
    page=fetch(SOURCES["tiktok"])
    # Public page markup may contain user stats as JSON.
    for pattern in [
        r'"followerCount"\s*:\s*("[\d.]+"|\d+)',
        r'\\"followerCount\\"\s*:\s*(?:\\"([\d.]+)\\"|(\d+))',
    ]:
        m=re.search(pattern,page)
        if m:
            n=integer(next((v for v in m.groups() if v),None))
            if n is not None:
                result={"followers":n}
                for key, dest in (("heartCount","likes"),("videoCount","posts")):
                    p=re.search(r'"'+key+r'"\s*:\s*("[\d.]+"|\d+)',page)
                    if p:
                        v=integer(p.group(1).strip('"'))
                        if v is not None: result[dest]=v
                return result
    raise ValueError("Публичный HTML TikTok не содержит читаемых показателей")

def collect_instagram():
    """Try publicly exposed profile metadata and Instagram's own public web endpoint.

    This may be blocked in GitHub Actions and must never fake a success.
    """
    username = "realcoldcarti"
    page = fetch(SOURCES["instagram"])
    # Basic guard to avoid counting an unrelated page / login redirect.
    if username not in html.unescape(page).lower():
        raise ValueError("Instagram: profile identity not visible in public HTML")
    meta = re.search(
        r'<meta[^>]+(?:property|name)=[\"\'](?:og:description|description)[\"\'][^>]+content=[\"\']([^\"\']+)',
        page, re.I,
    )
    if not meta:
        meta = re.search(
            r'<meta[^>]+content=[\"\']([^\"\']+)[\"\'][^>]+(?:property|name)=[\"\'](?:og:description|description)',
            page, re.I,
        )
    if meta:
        description = html.unescape(meta.group(1))
        m = re.search(r'([\d,.]+\s*[kKmM]?)\s+Followers\b', description, re.I)
        if m:
            count = integer(m.group(1))
            if count is not None:
                result = {"followers": count, "approximate": bool(re.search(r'[kKmM]',m.group(1)))}
                posts = re.search(r'([\d,.]+\s*[kKmM]?)\s+Posts\b',description,re.I)
                if posts:
                    n = integer(posts.group(1))
                    if n is not None: result["posts"] = n
                return result
    # Meta sometimes embeds exact public counts in page data.
    for pattern in [
        r'"edge_followed_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"follower_count"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern,page)
        if m:
            n = integer(m.group(1))
            if n is not None: return {"followers": n}
    raise ValueError("Instagram: no readable public follower metric in HTML; login may be required")


def collect_yandex():
    """Read monthly listeners from the public artist brief-info API, if exposed.

    This is an unofficial endpoint and may change or reject unauthenticated requests.
    """
    api = "https://api.music.yandex.net/artists/6487554/brief-info"
    errors = []
    for url in (api,):
        try:
            data = json.loads(fetch(url))
            result = data.get("result") or {}
            if not isinstance(result,dict): raise ValueError("invalid API result")
            artist = result.get("artist") or {}
            if isinstance(artist,dict) and artist.get("id") is not None and str(artist["id"]) != "6487554":
                raise ValueError("unexpected artist ID")
            stats = result.get("stats") or {}
            if not isinstance(stats,dict): raise ValueError("no stats object")
            n = integer(stats.get("lastMonthListeners"))
            if n is None: n = integer(stats.get("last_month_listeners"))
            if n is not None:
                return {"listeners_monthly":n}
            errors.append("brief-info: missing lastMonthListeners")
        except Exception as exc:
            errors.append("brief-info: " + str(exc)[:180])
    # Conservative fallback to exact labeled public page text.
    try:
        page = fetch(SOURCES["yandex"])
        plain = re.sub(r"<[^>]+>"," ",html.unescape(page))
        plain = re.sub(r"\s+"," ",plain)
        for pattern in (
            r'([\d][\d\s,.]*(?:\s*(?:тыс\.?|млн\.?|[KkMm]))?)\s+слушател(?:ей|я)\s+в\s+месяц',
            r'([\d][\d\s,.]*(?:\s*(?:тыс\.?|млн\.?|[KkMm]))?)\s+monthly\s+listeners',
        ):
            m = re.search(pattern,plain,re.I)
            if m:
                n = integer(m.group(1))
                if n is not None: return {"listeners_monthly":n}
    except Exception as exc:
        errors.append("web page: " + str(exc)[:180])
    raise ValueError("Yandex public monthly listeners unavailable: " + "; ".join(errors))

COLLECTORS = {"telegram":collect_telegram,"yandex":collect_yandex,"instagram":collect_instagram,"tiktok":collect_tiktok}

def main():
    now=datetime.now(timezone.utc)
    day=now.date().isoformat()
    if DATA.exists():
        dataset=json.loads(DATA.read_text(encoding="utf-8"))
    else: dataset={"snapshots":[]}
    if not isinstance(dataset.get("snapshots"),list):
        raise ValueError("Неверная структура metrics.json")
    snapshots=dataset["snapshots"]
    summary={}
    successes=0
    for name, fn in COLLECTORS.items():
        try:
            measurements=fn()
            if not measurements: raise ValueError("Пустой результат")
            row={"date":day,"platform":name,**measurements,"status":"ok","source":SOURCES[name],"collected_at":now.isoformat()}
            snapshots[:]=[old for old in snapshots if not (old.get("date")==day and old.get("platform")==name)]
            snapshots.append(row)
            summary[name]={"status":"ok","checked_at":now.isoformat(),"metrics":list(measurements)}
            successes+=1
            print(f"{name}: OK {measurements}")
        except Exception as exc:
            msg=f"{type(exc).__name__}: {exc}"
            summary[name]={"status":"unavailable","checked_at":now.isoformat(),"message":msg}
            print(f"{name}: UNAVAILABLE {msg}")
    summary["vk"]={"status":"disabled","message":"Отключено по решению владельца проекта"}
    dataset["snapshots"].sort(key=lambda row:(row.get("date",""),row.get("platform","")))
    DATA.write_text(json.dumps(dataset,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    STATUS.write_text(json.dumps({"last_run":now.isoformat(),"successful_sources":successes,"sources":summary},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if successes==0:
        print("WARNING: Нет новых метрик; предыдущая история сохранена.")

if __name__=="__main__": main()

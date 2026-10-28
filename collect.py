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
    page=fetch(SOURCES["instagram"])
    # Do not treat arbitrary counters from unrelated posts as profile followers.
    patterns=[
        r'"edge_followed_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"follower_count"\s*:\s*(\d+)',
        r'([\d,.]+\s*[KkMm]?)\s+Followers\b',
    ]
    for pattern in patterns:
        m=re.search(pattern,page,re.I)
        if m:
            n=integer(m.group(1))
            if n is not None: return {"followers":n}
    raise ValueError("Публичная статистика Instagram недоступна без авторизации")

def collect_yandex():
    page=fetch(SOURCES["yandex"])
    # Only a metric explicitly labeled as monthly listeners qualifies.
    plain=re.sub(r"<[^>]*>"," ",html.unescape(page))
    plain=re.sub(r"\s+"," ",plain)
    patterns=[
        r'([\d][\d\s,.]*(?:\s*(?:тыс\.?|млн\.?|[KkMm]))?)\s+слушател(?:ей|я)\s+в\s+месяц',
        r'([\d][\d\s,.]*(?:\s*(?:тыс\.?|млн\.?|[KkMm]))?)\s+monthly\s+listeners',
    ]
    for pattern in patterns:
        m=re.search(pattern,plain,re.I)
        if m:
            n=integer(m.group(1))
            if n is not None: return {"listeners_monthly":n}
    raise ValueError("Публичная страница не отдала показатель слушателей в месяц")

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

#!/usr/bin/env python3
"""Cold Carti public analytics, no login or third-party paid scraping services.

Collects Telegram/TikTok directly. Tries Instagram's public profile endpoint,
public HTML, then browser rendering. Tries Yandex's artist API, public page,
and browser rendering. Each collector fails independently, history is preserved.
"""
import html
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_FILE = Path('metrics.json')
STATUS_FILE = Path('status.json')
ARTIST_ID = '6487554'
IG_USERNAME = 'realcoldcarti'
SOURCES = {
    'telegram': 'https://t.me/coldcarti',
    'tiktok': 'https://www.tiktok.com/@coldcarti',
    'instagram': f'https://www.instagram.com/{IG_USERNAME}/',
    'yandex': f'https://music.yandex.ru/artist/{ARTIST_ID}',
}
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/131.0.0.0 Safari/537.36')
IG_HEADERS = {'X-IG-App-ID': '936619743392459', 'Referer': SOURCES['instagram']}


class Unavailable(Exception):
    pass


def fetch(url, headers=None):
    request_headers = {
        'User-Agent': UA,
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8',
        'Accept': 'text/html,application/json;q=0.9,*/*;q=0.8',
    }
    request_headers.update(headers or {})
    req = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=22) as resp:
            content = resp.read(3_000_000)
            return content.decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        if exc.code == 451:
            raise Unavailable('HTTP 451: доступ ограничен по региону/юрисдикции') from exc
        raise Unavailable(f'HTTP {exc.code}') from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise Unavailable(f'сеть: {type(exc).__name__}') from exc


def number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and 0 <= value < 10**12:
        return int(value)
    if not isinstance(value, str):
        return None
    s = value.replace('\u00a0', '').replace('\u202f', '').strip()
    match = re.fullmatch(r'([\d\s.,]+)\s*(тыс\.?|тысяч|млн\.?|[kKmMкКмМ])?', s, re.I)
    if not match:
        return None
    raw = match.group(1).replace(' ', '').replace(',', '.')
    unit = (match.group(2) or '').lower().replace('.', '')
    if unit:
        try:
            v = float(raw)
        except ValueError:
            return None
        mult = 1_000_000 if unit in ('млн', 'м', 'm') else 1000
        n = round(v * mult)
    else:
        if '.' in raw:
            if re.fullmatch(r'\d{1,3}(?:\.\d{3})+', raw):
                raw = raw.replace('.', '')
            else:
                return None
        n = int(raw)
    return n if 0 <= n < 10**12 else None


def try_browser(url, evaluator=None):
    """Best-effort real browser. Never asks for credentials or solves challenges."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Unavailable('Playwright не установлен в GitHub Actions')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
            try:
                context = browser.new_context(
                    locale='en-US', user_agent=UA,
                    viewport={'width': 1280, 'height': 900})
                page = context.new_page()
                response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
                if response and response.status == 451:
                    raise Unavailable('HTTP 451: браузер тоже заблокирован по региону')
                page.wait_for_timeout(2500)
                result = None
                if evaluator:
                    try:
                        result = page.evaluate(evaluator)
                    except Exception:
                        pass
                result = result or {}
                result['html'] = page.content()[:3_000_000]
                result['text'] = page.locator('body').inner_text(timeout=6000)[:150_000]
                return result
            finally:
                browser.close()
    except Unavailable:
        raise
    except Exception as exc:
        raise Unavailable('браузер: ' + str(exc)[:130]) from exc


def collect_telegram():
    page = html.unescape(fetch(SOURCES['telegram']))
    match = re.search(r'([\d][\d\s\u00a0,.]*)\s+subscribers\b', page, re.I)
    val = number(match.group(1).strip()) if match else None
    if val is None or val < 1:
        raise Unavailable('Telegram: подписчики не найдены')
    return {'followers': val}


def collect_tiktok():
    page = fetch(SOURCES['tiktok'])
    match = re.search(r'"followerCount"\s*:\s*"?(\d+)"?', page)
    if not match:
        match = re.search(r'\\"followerCount\\"\s*:\s*\\?"?(\d+)', page)
    val = number(match.group(1)) if match else None
    if val is None:
        raise Unavailable('TikTok: число подписчиков не найдено')
    result = {'followers': val}
    for src, dest in [('heartCount', 'likes'), ('videoCount', 'posts')]:
        m = re.search(r'"' + src + r'"\s*:\s*"?(\d+)"?', page)
        if m:
            n = number(m.group(1))
            if n is not None:
                result[dest] = n
    return result


def instagram_api_json(payload):
    if not isinstance(payload, dict):
        return None
    user = ((payload.get('data') or {}).get('user') or (payload.get('user') or {}))
    if not isinstance(user, dict):
        return None
    if str(user.get('username', '')).lower() != IG_USERNAME:
        return None
    edge = user.get('edge_followed_by') or {}
    followers = number(edge.get('count')) if isinstance(edge, dict) else None
    if followers is None:
        followers = number(user.get('follower_count'))
    if followers is None:
        return None
    result = {'followers': followers}
    post_edge = user.get('edge_owner_to_timeline_media') or {}
    posts = number(post_edge.get('count')) if isinstance(post_edge, dict) else None
    if posts is None:
        posts = number(user.get('media_count'))
    if posts is not None:
        result['posts'] = posts
    return result


def instagram_meta(page):
    if IG_USERNAME not in html.unescape(page).lower():
        return None
    metas = re.findall(r'<meta\b[^>]*>', page, re.I)
    for tag in metas:
        if not re.search(r'(?:og:description|name=["\']description["\'])', tag, re.I):
            continue
        m = re.search(r'content\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        if not m:
            continue
        info = html.unescape(m.group(1))
        follower_match = re.search(
            r'([\d,.]+\s*[kKmM]?)\s+(?:Followers|подписчиков)', info, re.I)
        if follower_match:
            n = number(follower_match.group(1))
            if n is None:
                continue
            result = {
                'followers': n,
                'approximate': bool(re.search(r'[kKmM]', follower_match.group(1)))}
            pm = re.search(r'([\d,.]+\s*[kKmM]?)\s+(?:Posts|публикаций)', info, re.I)
            if pm:
                posts = number(pm.group(1))
                if posts is not None:
                    result['posts'] = posts
            return result
    return None


def collect_instagram():
    errors = []
    for host in ('https://i.instagram.com', 'https://www.instagram.com'):
        try:
            raw = fetch(
                host + '/api/v1/users/web_profile_info/?username=' + IG_USERNAME,
                IG_HEADERS)
            parsed = instagram_api_json(json.loads(raw))
            if parsed:
                return parsed
            errors.append('web_profile_info: нет данных аккаунта')
        except (Unavailable, ValueError) as e:
            errors.append('web_profile_info: ' + str(e))
    try:
        page = fetch(SOURCES['instagram'])
        parsed = instagram_meta(page)
        if parsed:
            return parsed
        errors.append('HTML: метрики скрыты')
    except Unavailable as e:
        errors.append('HTML: ' + str(e))
    js = """async () => {
      try {
        const r = await fetch('/api/v1/users/web_profile_info/?username=realcoldcarti', {
          headers: {'X-IG-App-ID':'936619743392459'}, credentials:'include'});
        if (!r.ok) return {apiStatus:r.status};
        return {apiData:await r.json(), apiStatus:r.status};
      } catch(e) { return {apiError:String(e).slice(0,80)}; }
    }"""
    try:
        info = try_browser(SOURCES['instagram'], js)
        parsed = instagram_api_json(info.get('apiData'))
        if parsed:
            return parsed
        parsed = instagram_meta(info.get('html', ''))
        if parsed:
            return parsed
        errors.append(
            'Chromium: API ' + str(info.get('apiStatus', 'нет ответа')) +
            ', публичная метрика отсутствует')
    except Unavailable as e:
        errors.append(str(e))
    try:
        return collect_instagram_mirror()
    except Unavailable as e:
        errors.append(str(e))
    raise Unavailable(
        'Instagram недоступен без входа: ' + '; '.join(errors)[-400:])


def find_monthly_in_text(text):
    text = re.sub(r'\s+', ' ', html.unescape(text))
    pats = [
        r'([\d][\d\s\u00a0,.]*(?:\s*(?:тыс\.?|млн\.?|[KkMm]))?)\s+(?:слушателей|слушателя|слушатель)\s+в\s+месяц',
        r'([\d][\d\s\u00a0,.]*(?:\s*(?:тыс\.?|млн\.?|[KkMm]))?)\s+monthly\s+listeners',
        r'(?:слушателей\s+за\s+месяц|слушателей\s+в\s+месяц|monthly\s+listeners)\s*[:：]?\s*([\d][\d\s,.]*(?:\s*(?:тыс\.?|млн\.?|[KkMm]))?)',
    ]
    for pat in pats:
        m = re.search(pat, text, re.I)
        if m:
            n = number(m.group(1).strip())
            if n is not None and n > 0:
                return {'listeners_monthly': n}
    return None


def yandex_json(payload):
    if not isinstance(payload, dict):
        return None
    result = payload.get('result') or payload
    if not isinstance(result, dict):
        return None
    artist = result.get('artist') or {}
    if isinstance(artist, dict) and artist.get('id') not in (
            None, ARTIST_ID, int(ARTIST_ID)):
        return None
    stats = result.get('stats') or (
        artist.get('stats') if isinstance(artist, dict) else {}) or {}
    if not isinstance(stats, dict):
        return None
    n = number(stats.get('lastMonthListeners'))
    if n is None:
        n = number(stats.get('last_month_listeners'))
    if n is None or n <= 0:
        return None
    return {'listeners_monthly': n}


def yandex_embedded(page):
    decoded = html.unescape(page)
    for pat in [
        r'"lastMonthListeners"\s*:\s*(\d+)',
        r'"last_month_listeners"\s*:\s*(\d+)']:
        m = re.search(pat, decoded)
        if m:
            n = number(m.group(1))
            if n and n > 0:
                return {'listeners_monthly': n}
    return find_monthly_in_text(re.sub(r'<[^>]*>', ' ', decoded))



def collect_yandex_mirror():
    """Non-official public catalogue, clearly marked as unverified."""
    url = 'https://lmusic.kz/artist/cold-carti'
    content = fetch(url)
    plain = re.sub(r'<[^>]+>', ' ', html.unescape(content))
    plain = re.sub(r'\\s+', ' ', plain)
    # Match the exact artist and two known releases, not an unrelated namesake.
    if not re.search(r'Cold\\s+Carti', plain, re.I):
        raise Unavailable('зеркало: имя артиста не найдено')
    if not ('я не могу уснуть' in plain.lower() and 'падаем' in plain.lower()):
        raise Unavailable('зеркало: релизы не совпали')
    match = re.search(
        r'([\\d\\s\\u00a0\\u202f.,]+)\\s+слушател(?:ь|ей|я)\\s+за\\s+месяц',
        plain, re.I)
    n = number(match.group(1).strip()) if match else None
    if not n or n <= 0:
        raise Unavailable('зеркало: нет числа месячных слушателей')
    return {
        'listeners_monthly': n,
        '_source': url,
        '_source_note': 'Внешний каталог lmusic.kz. Метрика не подтверждена Яндекс Музыкой.',
        '_quality': 'external_unverified',
    }


def collect_instagram_mirror():
    """Public third-party profile page, no account connection."""
    url = 'https://imginn.com/realcoldcarti/'
    page = html.unescape(fetch(url))
    plain = re.sub(r'<[^>]+>', ' ', page)
    plain = re.sub(r'\\s+', ' ', plain)
    match_user = re.search(r'@realcoldcarti\\b', plain, re.I)
    if not match_user:
        raise Unavailable('публичный просмотрщик: профиль не найден')
    segment = plain[match_user.start(): match_user.start() + 650]
    match = re.search(r'([\\d,.]+\\s*[KkMm]?)\\s+followers\\b', segment, re.I)
    n = number(match.group(1).strip()) if match else None
    if not n or n <= 0:
        raise Unavailable('публичный просмотрщик: подписчики не отображаются')
    return {
        'followers': n,
        '_source': url,
        '_source_note': 'Внешняя публичная страница, не данные Instagram API.',
        '_quality': 'external_unverified',
    }


def collect_yandex():
    errs = []
    api = 'https://api.music.yandex.net/artists/' + ARTIST_ID + '/brief-info'
    try:
        obj = json.loads(fetch(api))
        data = yandex_json(obj)
        if data:
            return data
        errs.append('API: нет статистики')
    except (Unavailable, ValueError) as e:
        errs.append('API: ' + str(e))
    try:
        page = fetch(SOURCES['yandex'])
        data = yandex_embedded(page)
        if data:
            return data
        if 'not available in your region' in page.lower():
            errs.append('сайт: недоступен в регионе')
        else:
            errs.append('сайт: нет числа слушателей')
    except Unavailable as e:
        errs.append('сайт: ' + str(e))
    try:
        browser = try_browser(SOURCES['yandex'])
        data = yandex_embedded(browser.get('html', '')) or find_monthly_in_text(
            browser.get('text', ''))
        if data:
            return data
        if 'not available in your region' in browser.get('text', '').lower():
            errs.append('Chromium: страница недоступна в регионе')
        else:
            errs.append('Chromium: нет числа слушателей')
    except Unavailable as e:
        errs.append('Chromium: ' + str(e))
    try:
        return collect_yandex_mirror()
    except Unavailable as e:
        errs.append('публичный каталог: ' + str(e))
    raise Unavailable('Яндекс Музыка: ' + '; '.join(errs))


COLLECTORS = {
    'telegram': collect_telegram,
    'yandex': collect_yandex,
    'instagram': collect_instagram,
    'tiktok': collect_tiktok,
}


def main():
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    payload = (
        json.loads(DATA_FILE.read_text(encoding='utf-8'))
        if DATA_FILE.exists() else {'snapshots': []})
    snapshots = payload.setdefault('snapshots', [])
    if not isinstance(snapshots, list):
        raise ValueError('Некорректный metrics.json')
    statuses = {}
    successful = 0
    for platform, fn in COLLECTORS.items():
        try:
            measurements = fn()
            if not measurements:
                raise Unavailable('нет показателей')
            snapshots[:] = [
                r for r in snapshots
                if not (r.get('date') == today and r.get('platform') == platform)]
            origin = measurements.pop('_source', SOURCES[platform])
            note = measurements.pop('_source_note', '')
            quality = measurements.pop('_quality', 'public_platform')
            snapshots.append({
                'date': today, 'platform': platform,
                **measurements, 'status': 'ok',
                'source': origin, 'source_note': note,
                'quality': quality,
                'collected_at': now.isoformat()})
            statuses[platform] = {
                'status': 'ok', 'checked_at': now.isoformat(),
                'metrics': list(measurements)}
            successful += 1
            print(f'{platform}: OK {measurements}', flush=True)
        except Exception as exc:
            statuses[platform] = {
                'status': 'unavailable', 'checked_at': now.isoformat(),
                'message': str(exc)[:900]}
            print(
                f'{platform}: UNAVAILABLE {type(exc).__name__}: {str(exc)[:900]}',
                flush=True)
    statuses['vk'] = {
        'status': 'disabled', 'message': 'Не собираем по решению владельца'}
    snapshots.sort(key=lambda r: (r.get('date', ''), r.get('platform', '')))
    DATA_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    STATUS_FILE.write_text(
        json.dumps({
            'last_run': now.isoformat(), 'successful_sources': successful,
            'sources': statuses}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    print(
        f'Итого: {successful}/4 источников с новыми данными. История сохранена.',
        flush=True)


if __name__ == '__main__':
    main()

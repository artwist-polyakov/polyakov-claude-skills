#!/usr/bin/env python3
"""Yandex Webmaster — Alice (Share of Voice) efficiency extractor.

Не имеет публичного API: данные приходят в HTML через window._initData при SSR.
Авторизация — через cookie Session_id (длинноживущая, httpOnly).

Subcommands:
  fetch       — скачать страницу, извлечь alice.* из _initData, сохранить JSON
  sov         — Share-of-Voice: 12 недельных точек, TSV
  competitors — топ-10 сайтов в Alice (queries.GENERAL), TSV
  with-site   — запросы где наш сайт присутствует (hasOwnExamples), TSV
  without-site — запросы где наш сайт НЕ присутствует (noOwnExamples), TSV
  summary     — короткая сводка alertType + средний SoV + размеры списков

Кеш JSON действует 24 часа; fetch и --no-cache принудительно обновляют данные.
"""
import argparse
import errno
import http.client
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

CACHE_TTL = 24 * 60 * 60
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 30
MAX_RETRY_WAIT = 60
RETRY_STATUSES = {429, 500, 502, 503, 504}

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Python.org installs ship without a CA bundle. Try common system locations
# before failing back to Python defaults (which may also be empty).
_CA_CANDIDATES = [
    os.environ.get("SSL_CERT_FILE", ""),
    "/etc/ssl/cert.pem",                          # macOS, FreeBSD
    "/etc/ssl/certs/ca-certificates.crt",         # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",           # RHEL/CentOS
    "/opt/homebrew/etc/ca-certificates/cert.pem", # Homebrew arm64
    "/usr/local/etc/ca-certificates/cert.pem",    # Homebrew x86_64
]


def _build_ssl_context() -> ssl.SSLContext:
    for path in _CA_CANDIDATES:
        if path and os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()

ALICE_URL_TEMPLATE = (
    "https://webmaster.yandex.ru/site/{host_id}/efficiency/alice/"
    "?tab=GENERAL&tableType=GENERAL&onlyWithMySites=OFF"
)


def fail(msg, code=1):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------- HTML fetch ----------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Сохраняем ответ и распознаём вход/капчу без перехода с cookie.
        return None


def atomic_write(path: str, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(path),
                                         prefix=f".{os.path.basename(path)}.",
                                         delete=False) as f:
            temporary = f.name
            f.write(data)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def wait_before_retry(attempt: int, message: str, retry_after=None) -> None:
    if attempt + 1 >= MAX_ATTEMPTS:
        fail(f"{message}; исчерпаны {MAX_ATTEMPTS} попытки загрузки")
    delay = 2 ** attempt
    if retry_after:
        try:
            if retry_after.strip().isdigit():
                delay = int(retry_after)
            else:
                delay = max(0, parsedate_to_datetime(retry_after).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            pass
    if delay > MAX_RETRY_WAIT:
        fail(f"{message}; Retry-After больше {MAX_RETRY_WAIT} секунд — повторите позже")
    print(f"{message}; повтор через {delay:.0f} с", file=sys.stderr)
    time.sleep(delay)


def temporary_network_error(error) -> bool:
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLError):
        return False
    return isinstance(reason, (TimeoutError, ConnectionError, http.client.IncompleteRead)) or (
        isinstance(reason, OSError) and reason.errno in {
            errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED,
            errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPIPE, socket.EAI_AGAIN,
        }
    )


def fetch_html(host_id: str, session_id: str, raw_path: str) -> str:
    url = ALICE_URL_TEMPLATE.format(host_id=host_id)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru,en;q=0.9",
            "Cookie": f"Session_id={session_id}",
        },
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_build_ssl_context()), _NoRedirect()
    )
    for attempt in range(MAX_ATTEMPTS):
        try:
            try:
                response = opener.open(req, timeout=REQUEST_TIMEOUT)
            except urllib.error.HTTPError as error:
                response = error  # Тело HTTP-ошибки тоже нужно для диагностики.
            with response as resp:
                body = resp.read()
                status = resp.code
                headers = resp.headers
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            if isinstance(reason, ssl.SSLError):
                fail(f"Ошибка TLS/сертификата: {reason}")
            message = f"Ошибка сети: {reason}"
            if not temporary_network_error(error):
                fail(message)
            wait_before_retry(attempt, message)
            continue

        # Только полностью прочитанный ответ заменяет предыдущий raw.html.
        atomic_write(raw_path, body)
        try:
            html = body.decode(headers.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            fail("Неизвестная кодировка HTML; проверьте raw.html")
        location = urllib.parse.urljoin(url, headers.get("Location", ""))
        target = urllib.parse.urlsplit(location)
        if "showcaptcha" in target.path.lower() or re.search(
            r"<(?:form|div)\b[^>]*captcha", html, re.IGNORECASE
        ):
            fail("Страница капчи — пройдите проверку в браузере и повторите запрос")
        if status == 401 or (target.hostname or "").startswith("passport.yandex."):
            fail("Перенаправление на вход или истёкшая сессия — обновите Session_id")
        if 300 <= status < 400:
            fail(f"Неожиданное перенаправление HTTP {status}; проверьте raw.html")
        if status in RETRY_STATUSES:
            wait_before_retry(attempt, f"HTTP {status}", headers.get("Retry-After"))
            continue
        if status >= 400:
            fail(f"HTTP {status} при загрузке Алисы; проверьте raw.html")
        return html


# ---------- _initData extraction ----------

def extract_init_data(html: str) -> dict:
    assignment = re.search(r"window\._initData\s*=\s*", html)
    if not assignment:
        fail("window._initData не найден — формат страницы изменился; проверьте raw.html")
    try:
        init, _ = json.JSONDecoder().raw_decode(html, assignment.end())
    except json.JSONDecodeError as e:
        fail(f"Некорректный JSON window._initData: {e}; проверьте raw.html")
    if not isinstance(init, dict):
        fail("window._initData должен быть объектом; проверьте raw.html")
    return init


def assert_authed(init_data: dict) -> None:
    if not init_data.get("userIsAuth"):
        fail("userIsAuth=false — Session_id истёк или неверен; обновите cookie в браузере")


def valid_alice(alice) -> bool:
    """Проверяем форму коллекций, которые читают команды отчётов."""
    if not isinstance(alice, dict) or not alice:
        return False

    def objects(value):
        return value is None or (
            isinstance(value, list) and all(isinstance(item, dict) for item in value)
        )

    if not objects(alice.get("sov")):
        return False
    queries = alice.get("queries")
    if queries is None:
        return True
    if not isinstance(queries, dict) or not objects(queries.get("GENERAL")):
        return False
    examples = queries.get("EXAMPLES")
    if examples is None:
        return True
    if not isinstance(examples, dict):
        return False
    for field in ("hasOwnExamples", "noOwnExamples"):
        items = examples.get(field)
        if not objects(items) or any(not objects(item.get("urls")) for item in items or []):
            return False
    return True


# ---------- Cache I/O ----------

def cache_path(cache_dir: str, host_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", host_id)
    d = os.path.join(cache_dir, f"host_{safe}", "alice")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "init.json")


def load_or_fetch(args, force=False) -> dict:
    """Returns alice dict, using cache when fresh."""
    path = cache_path(args.cache_dir, args.host_id)
    if not force and not getattr(args, "no_cache", False):
        try:
            if 0 <= time.time() - os.path.getmtime(path) < CACHE_TTL:
                with open(path, "r", encoding="utf-8") as f:
                    alice = json.load(f)
                if valid_alice(alice):
                    return alice
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    if not args.session_id:
        fail("SESSION_ID is required for fetch (set in config/.env)")

    raw_path = os.path.join(os.path.dirname(path), "raw.html")
    html = fetch_html(args.host_id, args.session_id, raw_path)
    init = extract_init_data(html)
    assert_authed(init)

    alice = init.get("alice")
    if not valid_alice(alice):
        fail("Некорректный или отсутствующий _initData.alice — проверьте сайт и raw.html")

    atomic_write(path, json.dumps(alice, ensure_ascii=False).encode("utf-8"))
    return alice


# ---------- Output helpers ----------

def tsv_line(row):
    return "\t".join("" if c is None else str(c).replace("\t", " ").replace("\n", " ").replace("\r", " ")
                     for c in row)


def write_tsv(rows, path: str):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(tsv_line(row) + "\n")


def emit_tsv(header, rows, out_path, head=20):
    write_tsv([header] + rows, out_path)
    print("\t".join(header))
    for r in rows[:head]:
        print(tsv_line(r))
    if len(rows) > head:
        print(f"... ({len(rows) - head} more rows, full data in: {out_path})")
    print(f"\nTotal: {len(rows)}")
    print(f"TSV: {out_path}")


# ---------- Subcommands ----------

def cmd_fetch(args):
    alice = load_or_fetch(args, force=True)
    path = cache_path(args.cache_dir, args.host_id)
    print(f"alice keys: {', '.join(alice.keys())}")
    print(f"sov points: {len(alice.get('sov') or [])}")
    q = alice.get("queries", {}) or {}
    general = q.get("GENERAL") or []
    examples = q.get("EXAMPLES") or {}
    has = examples.get("hasOwnExamples") or [] if isinstance(examples, dict) else []
    no = examples.get("noOwnExamples") or [] if isinstance(examples, dict) else []
    print(f"competitors (GENERAL): {len(general)}")
    print(f"with-site (hasOwnExamples): {len(has)}")
    print(f"without-site (noOwnExamples): {len(no)}")
    print(f"alertType: {alice.get('alertType')}")
    print(f"cached: {path}")


def cmd_sov(args):
    alice = load_or_fetch(args)
    sov = alice.get("sov") or []
    rows = []
    for p in sov:
        share = p.get("sharePercent")
        rows.append([
            p.get("dateFrom", ""),
            p.get("dateTo", ""),
            f"{share:.4f}" if isinstance(share, (int, float)) else "",
            f"{share * 100:.2f}%" if isinstance(share, (int, float)) else "",
        ])
    out = os.path.join(os.path.dirname(cache_path(args.cache_dir, args.host_id)), "sov.tsv")
    emit_tsv(["date_from", "date_to", "share", "share_pct"], rows, out, head=20)


def cmd_competitors(args):
    alice = load_or_fetch(args)
    general = ((alice.get("queries") or {}).get("GENERAL")) or []
    rows = [[i + 1, item.get("url", "")] for i, item in enumerate(general)]
    out = os.path.join(os.path.dirname(cache_path(args.cache_dir, args.host_id)), "competitors.tsv")
    emit_tsv(["rank", "url"], rows, out, head=20)


def _flatten_examples(items):
    """Each item: {query, urls: [{url,title,host,favicon}]}.
    TSV: query, rank_in_query, host, url, title."""
    rows = []
    for item in items or []:
        q = item.get("query", "")
        urls = item.get("urls") or []
        for i, u in enumerate(urls, start=1):
            rows.append([
                q,
                i,
                u.get("host", ""),
                u.get("url", ""),
                u.get("title", ""),
            ])
    return rows


def cmd_with_site(args):
    alice = load_or_fetch(args)
    ex = ((alice.get("queries") or {}).get("EXAMPLES")) or {}
    items = ex.get("hasOwnExamples") or []
    rows = _flatten_examples(items)
    out = os.path.join(os.path.dirname(cache_path(args.cache_dir, args.host_id)), "with_site.tsv")
    emit_tsv(["query", "rank", "host", "url", "title"], rows, out, head=15)
    print(f"Unique queries: {len(items)}")


def cmd_without_site(args):
    alice = load_or_fetch(args)
    ex = ((alice.get("queries") or {}).get("EXAMPLES")) or {}
    items = ex.get("noOwnExamples") or []
    rows = _flatten_examples(items)
    out = os.path.join(os.path.dirname(cache_path(args.cache_dir, args.host_id)), "without_site.tsv")
    emit_tsv(["query", "rank", "host", "url", "title"], rows, out, head=15)
    print(f"Unique queries: {len(items)}")


def cmd_summary(args):
    alice = load_or_fetch(args)
    sov = alice.get("sov") or []
    shares = [p.get("sharePercent") for p in sov if isinstance(p.get("sharePercent"), (int, float))]
    avg = sum(shares) / len(shares) if shares else 0
    last = shares[-1] if shares else 0
    first = shares[0] if shares else 0
    q = alice.get("queries") or {}
    general = q.get("GENERAL") or []
    ex = q.get("EXAMPLES") or {}
    has = ex.get("hasOwnExamples") or []
    no = ex.get("noOwnExamples") or []
    print(f"alertType:        {alice.get('alertType')}")
    print(f"sov points:       {len(sov)}")
    if sov:
        print(f"sov range:        {sov[0].get('dateFrom')} → {sov[-1].get('dateTo')}")
        print(f"sov first:        {first * 100:.2f}%")
        print(f"sov last:         {last * 100:.2f}%")
        print(f"sov avg:          {avg * 100:.2f}%")
    print(f"competitors:      {len(general)}")
    print(f"with-site qs:     {len(has)}")
    print(f"without-site qs:  {len(no)}")


# ---------- CLI ----------

def build_parser():
    p = argparse.ArgumentParser(description="Yandex Webmaster Alice efficiency (SSR scraper)")
    p.add_argument("--host-id", required=True, help="Host id, e.g. https:metallik.ru:443")
    p.add_argument("--session-id", default=os.environ.get("SESSION_ID", ""),
                   help="Session_id cookie (default: $SESSION_ID)")
    p.add_argument("--cache-dir", required=True, help="Cache directory root")
    p.add_argument("--no-cache", action="store_true", help="Обновить кеш, сохранив прежний при ошибке")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="force refresh + summary").set_defaults(func=cmd_fetch)
    sub.add_parser("sov", help="Share-of-Voice timeline").set_defaults(func=cmd_sov)
    sub.add_parser("competitors", help="Top sites in Alice").set_defaults(func=cmd_competitors)
    sub.add_parser("with-site", help="queries where own site appears").set_defaults(func=cmd_with_site)
    sub.add_parser("without-site", help="queries where own site is absent").set_defaults(func=cmd_without_site)
    sub.add_parser("summary", help="short summary").set_defaults(func=cmd_summary)
    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except OSError as error:
        fail(f"Ошибка чтения или записи файлов: {error}")


if __name__ == "__main__":
    main()

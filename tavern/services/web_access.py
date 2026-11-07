"""The internet for the assistant (stage 10ж): guarded download, readable text from a page, web search.

Whatever the model (or a link in a skill) asks for goes through `fetch`, which is the only door:
- http and https only; no login in the address; no cookies; no proxies from the environment; the
  only thing sent out is the address asked for (and, for a search, the words the model chose);
- the host is resolved here and the connection is made to that very address, so a name that
  answers with a private address the second time (DNS rebinding) cannot lead inside; loopback,
  private, link-local (the cloud metadata address included), shared and reserved addresses are
  refused, for the first address and for every redirect (at most MAX_REDIRECTS);
- size and time limits; a page bigger than the limit is read in part and says so;
- the one exception: the address of the user's own search engine (settings), which may be on their
  home network. A page the model asks for never gets that right.

Text that comes back is somebody else's text: callers hand it to the model marked as data.

Search: a keyless engine's plain HTML page by default (DuckDuckGo). Its markup is not a promise;
`parse_duckduckgo` is the one function that breaks when it changes, and then says so in words
instead of returning nothing. Another address (SearXNG, JSON) can be chosen in the settings.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlsplit, urlunsplit

import httpx

from tavern.errors import AppError

MAX_REDIRECTS = 5
PAGE_BYTES = 1_500_000  # a page is read up to this size
SKILL_BYTES = 200_000  # a skill file
TIMEOUT = 25.0  # seconds for one download, redirects included
PAGE_CHARS = 8_000  # characters of one "page" handed to the model
MAX_LINKS = 40
CACHE_SECONDS = 300
CACHE_SIZE = 8
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml", "application/rss",
              "application/atom", "application/yaml", "application/x-yaml", "application/markdown")
DEFAULT_SEARCH = "https://html.duckduckgo.com/html/"


class WebError(AppError):
    """The internet did not give what was asked; the message is Russian and says why."""

    status = 502
    code = "web_access"


# --- addresses --------------------------------------------------------------------------------

_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def blocked_reason(address: str) -> str:
    """Why a resolved address is not allowed ("" = it is a public one)."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return "это не адрес"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.is_loopback:
        return "адрес этого же устройства"
    if ip.is_link_local:
        return "служебный адрес сети (в том числе облачные метаданные)"
    if ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_unspecified or not ip.is_global:
        return "адрес внутри локальной сети"
    return ""


async def resolve(host: str) -> list[str]:
    """Every address of a host (a literal stays as it is). Replaced in tests."""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return [host.strip("[]")]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise WebError(f"Не удалось найти адрес «{host}» — проверьте ссылку и подключение к интернету") from exc
    found: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in found:
            found.append(address)
    if not found:
        raise WebError(f"Не удалось найти адрес «{host}»")
    return found


def _split(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, rest) of a link, or WebError."""
    if not isinstance(url, str) or not url.strip():
        raise WebError("Нужна ссылка, начинающаяся с http:// или https://")
    url = url.strip()
    if len(url) > 2000:
        raise WebError("Ссылка слишком длинная")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise WebError("Открываются только ссылки http:// и https://")
    if not parts.hostname:
        raise WebError("В ссылке нет адреса сайта")
    if parts.username or parts.password:
        raise WebError("Ссылки с логином и паролем не открываются")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise WebError("В ссылке неверный порт") from exc
    return parts.scheme, parts.hostname.lower(), port, urlunsplit(("", "", parts.path or "/", parts.query, ""))


async def _public_addresses(host: str, *, allow_private: bool) -> list[str]:
    addresses = await resolve(host)
    if allow_private:
        return addresses
    for address in addresses:
        reason = blocked_reason(address)
        if reason:
            raise WebError(f"Этот адрес закрыт: «{host}» указывает на {reason}. Помощник открывает только "
                           "сайты из интернета, ничего из вашей сети и с вашего устройства.")
    return addresses


# --- download ---------------------------------------------------------------------------------


@dataclass
class Fetched:
    url: str  # the address after redirects
    status: int
    content_type: str
    text: str
    truncated: bool = False
    size: int = 0  # bytes read
    headers: dict[str, str] = field(default_factory=dict)


def make_client() -> httpx.AsyncClient:
    """One client per download: no cookie jar kept, no proxy from the environment. Replaced in tests."""
    return httpx.AsyncClient(follow_redirects=False, trust_env=False, cookies=httpx.Cookies(),
                             timeout=httpx.Timeout(15.0, connect=10.0))


def _decode(raw: bytes, content_type: str) -> str:
    charset = re.search(r"charset=([\w-]+)", content_type, re.I)
    name = charset.group(1) if charset else "utf-8"
    if not charset:
        meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
        name = meta.group(1).decode("ascii", "ignore") if meta else "utf-8"
    try:
        return raw.decode(name, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


async def fetch(url: str, *, method: str = "GET", data: dict[str, str] | None = None,
                max_bytes: int | None = None, allow_hosts: frozenset[str] = frozenset(),
                accept: str = "text/html,application/xhtml+xml,text/plain,text/markdown;q=0.9,*/*;q=0.5",
                any_type: bool = False) -> Fetched:
    """Download one text document under the rules in the module's docstring.

    `allow_hosts`: hosts that may be on a private network (only the user's own search engine)."""
    try:
        async with asyncio.timeout(TIMEOUT):
            return await _fetch(url, method, data, max_bytes or PAGE_BYTES, allow_hosts, accept, any_type)
    except TimeoutError as exc:
        raise WebError("Сайт слишком долго не отвечает, попробуйте позже или другую ссылку") from exc


async def _fetch(url: str, method: str, data: dict[str, str] | None, max_bytes: int,
                 allow_hosts: frozenset[str], accept: str, any_type: bool) -> Fetched:
    current = url.strip()
    async with make_client() as client:
        for _ in range(MAX_REDIRECTS + 1):
            scheme, host, port, rest = _split(current)
            addresses = await _public_addresses(host, allow_private=host in allow_hosts)
            pinned = addresses[0]
            shown = f"[{pinned}]" if ":" in pinned else pinned
            default = port == (443 if scheme == "https" else 80)
            target = f"{scheme}://{shown}:{port}{rest}"
            headers = {"Host": host if default else f"{host}:{port}", "User-Agent": USER_AGENT, "Accept": accept,
                       "Accept-Language": "ru,en;q=0.8", "Accept-Encoding": "gzip, deflate"}
            extensions = {"sni_hostname": host} if scheme == "https" else {}
            try:
                async with client.stream(method, target, headers=headers, extensions=extensions,
                                         data=data if method == "POST" else None) as response:
                    client.cookies.clear()
                    if response.is_redirect and response.headers.get("location"):
                        current = urljoin(f"{scheme}://{host}{'' if default else f':{port}'}{rest}",
                                          response.headers["location"])
                        method, data = "GET", None
                        continue
                    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if (response.status_code < 400 and not any_type and kind
                            and not kind.startswith(TEXT_TYPES)):
                        raise WebError(f"Это не текстовая страница (тип «{kind}»): помощник читает только текст")
                    raw = bytearray()
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > max_bytes:
                            del raw[max_bytes:]
                            truncated = True
                            break
                    return Fetched(current, response.status_code, kind,
                                   _decode(bytes(raw), response.headers.get("content-type", "")), truncated,
                                   len(raw), dict(response.headers))
            except httpx.TooManyRedirects as exc:  # pragma: no cover - redirects are followed by hand
                raise WebError("Слишком много переходов по ссылке") from exc
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise WebError(f"Не удалось соединиться с «{host}»") from exc
            except httpx.TimeoutException as exc:
                raise WebError(f"Сайт «{host}» слишком долго отвечает") from exc
            except httpx.TransportError as exc:
                raise WebError(f"Сбой при загрузке с «{host}»: {type(exc).__name__}") from exc
    raise WebError(f"Больше {MAX_REDIRECTS} переходов по ссылке подряд — остановился")


def _hint(status: int) -> str:
    if status in (401, 403):
        return "сайт не пускает без входа или не отдаёт страницу автоматическим запросам"
    if status == 404:
        return "такой страницы нет"
    if status == 429:
        return "сайт просит не торопиться, попробуйте позже"
    return "сайт ответил ошибкой"


async def fetch_ok(url: str, **kwargs: Any) -> Fetched:
    got = await fetch(url, **kwargs)
    if got.status >= 400:
        raise WebError(f"Сайт ответил кодом {got.status}: {_hint(got.status)}")
    return got


# --- HTML to text -----------------------------------------------------------------------------


class _Readable(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "nav", "footer", "object"}
    BLOCKS = {"p", "div", "section", "article", "main", "header", "ul", "ol", "table", "tr", "blockquote", "pre",
              "form", "details", "figure", "dl", "dd", "dt", "hr"}

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: list[str] = []
        self.title = ""
        self.links: list[dict[str, str]] = []
        self._skip = 0
        self._pre = 0
        self._in_title = False
        self._href: str | None = None
        self._link_text: list[str] = []

    def _trailing(self) -> int:
        count = 0
        for piece in reversed(self.out):
            stripped = piece.rstrip("\n")
            count += len(piece) - len(stripped)
            if stripped:
                break
        return count

    def _break(self, count: int = 1) -> None:
        """A line break, or a blank line (count 2), without piling up empty lines."""
        if self.out:
            self.out.append("\n" * max(0, count - self._trailing()))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "br":
            self._break()
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._break(2)
            self.out.append("#" * int(tag[1]) + " ")
        elif tag == "li":
            self._break()
            self.out.append("- ")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in self.BLOCKS:
            self._break(2 if tag in ("p", "blockquote", "pre", "table") else 1)
        if tag == "pre":
            self._pre += 1
        if tag == "a" and a.get("href"):
            self._href = str(a["href"])
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre = max(0, self._pre - 1)
        if tag in self.BLOCKS or tag in ("h1", "h2", "h3", "h4", "h5", "h6", "li"):
            self._break()
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._link_text).split())
            target = urljoin(self.base, self._href)
            if text and urlsplit(target).scheme in ("http", "https") and len(self.links) < MAX_LINKS \
                    and not any(link["url"] == target for link in self.links):
                self.links.append({"text": text[:120], "url": target})
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if not self._pre:
            data = re.sub(r"\s+", " ", data)
            if not self.out or self._trailing():
                data = data.lstrip()
        if not data:
            return
        self.out.append(data)
        if self._href is not None:
            self._link_text.append(data)


def html_to_text(html: str, base: str = "") -> tuple[str, str, list[dict[str, str]]]:
    """(title, readable text, links) of an HTML page."""
    parser = _Readable(base)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - broken markup: keep what was read
        pass
    text = "".join(parser.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return " ".join(parser.title.split())[:200], text, parser.links


def is_html(got: Fetched) -> bool:
    return "html" in got.content_type or (not got.content_type and "<html" in got.text[:2000].lower())


def pages_of(text: str, size: int = PAGE_CHARS) -> list[str]:
    """The text in pages of about `size` characters, cut at a line break when there is one near."""
    pages: list[str] = []
    rest = text
    while len(rest) > size:
        cut = rest.rfind("\n", int(size * 0.7), size)
        cut = cut if cut > 0 else size
        pages.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    pages.append(rest)
    return pages


_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()


def clear_cache() -> None:
    _cache.clear()


async def read_page(url: str, page: int = 1) -> dict[str, Any]:
    """A page as text, `page` of `pages` (long pages are paged, never cut silently)."""
    now = time.monotonic()
    entry = _cache.get(url)
    if entry is None or now - entry[0] > CACHE_SECONDS:
        got = await fetch_ok(url)
        if is_html(got):
            title, text, links = html_to_text(got.text, got.url)
        else:
            title, text, links = "", got.text.strip(), []
        entry = (now, {"url": got.url, "title": title, "pages": pages_of(text), "links": links,
                       "truncated": got.truncated, "size": got.size, "chars": len(text)})
        _cache[url] = entry
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    data = entry[1]
    total = len(data["pages"])
    if not isinstance(page, int) or page < 1 or page > total:
        raise WebError(f"У страницы {total} частей; запросите часть от 1 до {total}")
    out: dict[str, Any] = {"url": data["url"], "title": data["title"], "page": page, "pages": total,
                           "text": data["pages"][page - 1] or "(на странице нет читаемого текста)"}
    if page == 1 and data["links"]:
        out["links"] = data["links"]
    notes = []
    if page < total:
        notes.append(f"Показана часть {page} из {total}; остальное НЕ показано. Чтобы читать дальше, "
                     f"вызовите read_page с page={page + 1}.")
    if data["truncated"]:
        notes.append(f"Страница больше {PAGE_BYTES // 1000} КБ: прочитано только её начало, остальное отброшено.")
    if notes:
        out["notice"] = " ".join(notes)
    return out


# --- search -----------------------------------------------------------------------------------


class _DuckResults(HTMLParser):
    """The results page of html.duckduckgo.com: title links `result__a`, snippets `result__snippet`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.no_results = False
        self._target: str | None = None  # "title" | "snippet"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if "no-results" in classes:
            self.no_results = True
        if tag == "a" and "result__a" in classes:
            self.results.append({"title": "", "url": _duck_target(a.get("href") or ""), "snippet": ""})
            self._target = "title"
        elif "result__snippet" in classes and self.results:
            self._target = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "div", "td") and self._target:
            self._target = None

    def handle_data(self, data: str) -> None:
        if self._target and self.results:
            self.results[-1][self._target] += data


def _duck_target(href: str) -> str:
    """The real address behind a DuckDuckGo redirect link."""
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com"):
        target = parse_qs(parts.query).get("uddg")
        return target[0] if target else ""  # an advertisement has no target
    return href


def parse_duckduckgo(html: str) -> list[dict[str, str]]:
    """Results of a DuckDuckGo HTML page. THE function that breaks when the page's markup changes: then it
    raises a WebError that says so, never an empty list that looks like "nothing found"."""
    parser = _DuckResults()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    found = []
    for item in parser.results:
        title = " ".join(item["title"].split())
        if title and item["url"].startswith(("http://", "https://")):
            found.append({"title": title[:200], "url": item["url"], "snippet": " ".join(item["snippet"].split())[:300]})
    if found:
        return found
    if parser.no_results:
        return []
    lowered = html.lower()
    if "anomaly" in lowered or "captcha" in lowered or "unusual traffic" in lowered:
        raise WebError("Поисковик принял запрос за автоматический и просит пройти проверку. Подождите немного или "
                       "укажите другой поиск (например, свой SearXNG) в настройках помощника.")
    raise WebError("Не удалось разобрать страницу поиска: сервис, вероятно, изменил вёрстку или не отдал результаты. "
                   "Это не значит, что ничего нет. Попробуйте позже или укажите другой поиск (например, свой "
                   "SearXNG) в настройках помощника.")


def parse_searxng(text: str) -> list[dict[str, str]]:
    try:
        data = json.loads(text)
        rows = data["results"]
    except (ValueError, KeyError, TypeError) as exc:
        raise WebError("Поиск по указанному адресу ответил не так, как отвечает SearXNG. Проверьте адрес и что в "
                       "его настройках разрешён формат json.") from exc
    found = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("url", "")).startswith(("http://", "https://")):
            found.append({"title": " ".join(str(row.get("title") or row["url"]).split())[:200], "url": str(row["url"]),
                          "snippet": " ".join(str(row.get("content") or "").split())[:300]})
    return found


def check_search_address(value: Any) -> str:
    """The address of the user's own search engine, checked for shape ("" = the default engine)."""
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise AppError("Адрес поиска должен быть текстом")
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise AppError("Адрес поиска: полная ссылка вида https://search.example.org или http://192.168.1.5:8080")
    return value.rstrip("/")


async def search(query: str, *, engine: str = "duckduckgo", address: str = "", count: int = 8) -> list[dict[str, str]]:
    """Results for the words the model chose. These words are all that leaves the app."""
    query = " ".join(str(query).split())
    if not query:
        raise WebError("Нужен поисковый запрос")
    query = query[:300]
    if engine == "searxng" and not address:
        raise WebError("Выбран свой SearXNG, но его адрес не указан: впишите его в настройках помощника.")
    if engine == "searxng":
        parts = urlsplit(address)
        got = await fetch_ok(f"{address}/search?q={quote(query)}&format=json", accept="application/json",
                             allow_hosts=frozenset({parts.hostname.lower()} if parts.hostname else set()))
        return parse_searxng(got.text)[:count]
    got = await fetch_ok(DEFAULT_SEARCH, method="POST", data={"q": query})
    return parse_duckduckgo(got.text)[:count]

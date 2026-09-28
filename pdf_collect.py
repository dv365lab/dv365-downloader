"""Collect every PDF linked from a web page and store the files locally.

The collector deliberately depends on nothing but the standard library, so it
stays independent from yt-dlp and is easy to test.  It never decides where
files go: the caller passes a directory it has already validated, and every
helper here is a small pure function where possible.
"""
from __future__ import annotations

import ipaddress
import os
import re
import time
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 dv365-downloader"
)
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_PDF_FILES = 400
MAX_CRAWL_PAGES = 25
CHUNK = 64 * 1024
TIMEOUT = 30
HEAD_SNIFF = 1024
# A link counts as a PDF when its path ends with ".pdf", optionally followed by
# a query string or fragment.  Lookalikes such as "report.pdf.txt" never match.
PDF_LINK = re.compile(r"\.pdf(?=[?#]|$)", re.IGNORECASE)
HTML_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
# Only these links are worth following when the user asks for subpages: an
# archive page almost always links to other files as well.
PAGE_SUFFIXES = {"html", "htm", "shtml", "xhtml", "php", "asp", "aspx", "jsp", "cgi"}
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
NAME_LIMIT = 150
# The publisher's older papers.htm page points its Mentor 09 slides at a
# nonexistent mentor13_ file; its current /html/papers.html page has this path.
KNOWN_PDF_PATH_FIXES = {
    ("lcdm-eng.com", "/papers/mentor13_U2U%202009%20presentation_v02.pdf"):
        "/papers/mentor09_U2U%202009%20presentation_v02.pdf",
}


class FetchError(RuntimeError):
    """Raised when a page or file cannot be retrieved."""


class Cancelled(RuntimeError):
    """Raised when the caller asked to stop in the middle of the work."""


def is_private_host(host: str | None) -> bool:
    """True for loopback, private, link-local and reserved hosts.

    Only literal host names are inspected: resolving names here would add a DNS
    round trip per link without closing the rebinding hole anyway.
    """
    if not host:
        return True
    host = host.strip().strip("[]").lower().rstrip(".")
    if not host or host in {"localhost", "localhost.localdomain"}:
        return True
    if host.endswith((".local", ".internal", ".localhost", ".home.arpa")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


def host_allowed(host: str | None, allow_private: bool) -> bool:
    """Pages the user pointed at directly may live on a LAN; discovered links may not."""
    if not host:
        return False
    return True if allow_private else not is_private_host(host)


def is_pdf_link(url: str) -> bool:
    return bool(PDF_LINK.search(url))


def normalize_url(url: str) -> str:
    """Percent-encode characters urllib refuses but every browser tolerates.

    Real archives contain links such as ``SNUG Design Reference.pdf`` written
    with a literal space, so the fragment is dropped and the rest is quoted
    without re-encoding escapes that are already present.
    """
    parts = urlsplit(url)
    return parts._replace(
        path=quote(parts.path, safe="/%:@!$&'()*+,;=~-._"),
        query=quote(parts.query, safe="=&%:@!$'()*+,;~-._/?[]"),
        fragment="",
    ).geturl()


class _RedirectGuard(HTTPRedirectHandler):
    """Reject redirects that leave http(s) or jump to an internal address."""

    def __init__(self, allow_private: bool):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme not in {"http", "https"} or not host_allowed(target.hostname, self.allow_private):
            raise FetchError("页面跳转到了不允许访问的地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(url: str, allow_private: bool):
    host = urlsplit(url).hostname or ""
    # Local addresses must never travel through a corporate or sandbox proxy.
    proxies = {} if is_private_host(host) else getproxies()
    return build_opener(ProxyHandler(proxies), _RedirectGuard(allow_private))


def _network_error(exc: BaseException) -> str:
    reason = getattr(exc, "reason", exc)
    return f"网络请求失败：{reason}"


def fetch(url: str, *, allow_private: bool, limit: int, accept: str = "*/*",
          timeout: int = TIMEOUT):
    """GET one resource and return ``(final_url, headers, body)``."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FetchError("只支持 http:// 或 https:// 地址。")
    if not host_allowed(parts.hostname, allow_private):
        raise FetchError("已跳过内网地址。")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with _opener(url, allow_private).open(request, timeout=timeout) as response:
            final = response.geturl()
            if urlsplit(final).scheme not in {"http", "https"}:
                raise FetchError("服务器返回了不支持的地址。")
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise FetchError("内容过大，已跳过。")
            body = response.read(limit + 1)
            if len(body) > limit:
                raise FetchError("内容过大，已跳过。")
            return final, response.headers, body
    except HTTPError as exc:
        exc.close()
        raise FetchError(f"服务器返回 {exc.code}。") from None
    except (URLError, OSError, ValueError) as exc:
        raise FetchError(_network_error(exc)) from None


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.base: str | None = None
        self._title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "base" and attributes.get("href"):
            self.base = attributes["href"]
        elif tag in {"a", "area"} and attributes.get("href"):
            self.links.append(attributes["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self._title_parts).split())[:200]


def page_title(parser: _LinkParser) -> str:
    return parser.title


def links_of(parser: _LinkParser, base_url: str) -> list[str]:
    """Absolute, de-duplicated http(s) links in document order."""
    root = parser.base or base_url
    try:
        root = urljoin(base_url, root)
    except ValueError:
        root = base_url
    result: list[str] = []
    seen: set[str] = set()
    for href in parser.links:
        value = href.strip()
        if not value or value.startswith(("#", "javascript:", "mailto:", "data:", "tel:")):
            continue
        try:
            absolute = urljoin(root, value)
        except ValueError:
            continue
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        absolute = normalize_url(absolute)
        if absolute not in seen:
            seen.add(absolute)
            result.append(absolute)
    return result


def parse_page(html_text: str, base_url: str) -> tuple[str, list[str]]:
    """Return ``(title, links)`` for one HTML document."""
    parser = _LinkParser()
    parser.feed(html_text)
    parser.close()
    return parser.title, links_of(parser, base_url)


def decode(body: bytes, headers) -> str:
    declared = headers.get("Content-Type") or ""
    match = re.search(r"charset=([\w.:+-]+)", declared, re.IGNORECASE)
    for candidate in (match.group(1).strip("\"'") if match else None, "utf-8", "gb18030"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def sanitize_filename(name: str) -> str:
    """Reduce an arbitrary string to a portable single file name."""
    name = unquote(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char >= " " and char not in '<>:"|?*')
    name = name.strip().lstrip(".").rstrip(".")
    if not name:
        return ""
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    if stem.upper() in RESERVED_NAMES:
        name = f"_{name}"
    if len(name) > NAME_LIMIT:
        keep = extension[:10]
        trimmed = name[: NAME_LIMIT - len(keep) - 1] if keep else name[:NAME_LIMIT]
        name = f"{trimmed.rstrip('.')}.{keep}" if keep else trimmed
    return name


def filename_for(url: str) -> str:
    """A deterministic local name derived from the link itself."""
    segments = [part for part in urlsplit(url).path.split("/") if part]
    name = sanitize_filename(segments[-1]) if segments else ""
    if not name:
        name = "document"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return sanitize_filename(name) or "document.pdf"


def _gbk_path_url(url: str) -> str | None:
    """Alternate URL for older servers whose non-ASCII file paths use GBK bytes."""
    parts = urlsplit(url)
    path = unquote(parts.path)
    if path.isascii():
        return None
    try:
        encoded = quote(path.encode("gbk"), safe="/")
    except UnicodeEncodeError:
        return None
    return parts._replace(path=encoded).geturl() if encoded != parts.path else None


def _open_pdf(url: str, allow_private: bool):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"}
    opener = _opener(url, allow_private)
    try:
        return opener.open(Request(url, headers=headers), timeout=TIMEOUT)
    except HTTPError as exc:
        alternate = _gbk_path_url(url) if exc.code == 404 else None
        if not alternate:
            raise
        exc.close()
        return opener.open(Request(alternate, headers=headers), timeout=TIMEOUT)


def unique_path(directory: Path, name: str, taken: set[str] | None = None) -> Path:
    """Pick a free path, appending ``(2)``, ``(3)``… for repeated names."""
    claimed = taken if taken is not None else set()
    candidate = directory / name
    if not candidate.exists() and candidate.name not in claimed:
        return candidate
    stem = candidate.stem
    for index in range(2, 1000):
        candidate = directory / f"{stem} ({index}){candidate.suffix}"
        if not candidate.exists() and candidate.name not in claimed:
            return candidate
    raise FetchError("同名文件过多，无法继续保存。")


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def download_pdf(url: str, destination: Path, *, allow_private: bool, should_stop,
                 on_progress=None) -> tuple[Path, int]:
    """Stream one PDF into ``destination``.

    Data lands in a ``.part`` sibling and is renamed only after the payload has
    been recognised as a PDF, so a failure or a cancellation never leaves a
    half-written file behind.
    """
    if not host_allowed(urlsplit(url).hostname, allow_private):
        raise FetchError("已跳过内网地址。")
    part = destination.with_name(destination.name + ".part")
    head = b""
    size = 0
    content_type = ""
    try:
        with _open_pdf(url, allow_private) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            with part.open("wb") as handle:
                while True:
                    if should_stop():
                        raise Cancelled
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    if len(head) < HEAD_SNIFF:
                        head += chunk[: HEAD_SNIFF - len(head)]
                    handle.write(chunk)
                    size += len(chunk)
                    if on_progress:
                        on_progress(size)
        if b"%PDF" not in head:
            raise FetchError("链接返回的是网页，不是 PDF 文件。" if content_type in HTML_TYPES or not content_type
                             else "链接返回的内容不是 PDF 文件。")
    except Cancelled:
        _remove(part)
        raise
    except FetchError:
        _remove(part)
        raise
    except HTTPError as exc:
        _remove(part)
        exc.close()
        raise FetchError(f"服务器返回 {exc.code}。") from None
    except (URLError, OSError, ValueError) as exc:
        _remove(part)
        raise FetchError(_network_error(exc)) from None
    os.replace(part, destination)
    return destination, size


def _same_section(link: str, start, prefix: str) -> bool:
    parts = urlsplit(link)
    return (parts.scheme in {"http", "https"} and parts.hostname == start.hostname
            and parts.path.startswith(prefix))


def looks_like_page(url: str) -> bool:
    """True for links that may hold more links rather than a downloadable file."""
    path = urlsplit(url).path
    if path.endswith("/"):
        return True
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return True
    return name.rsplit(".", 1)[-1].lower() in PAGE_SUFFIXES


def discover(page_url: str, *, include_subpages: bool = False, should_stop=None,
             max_files: int = MAX_PDF_FILES) -> tuple[str, list[str], int]:
    """Return ``(page_title, pdf_links, pages_scanned)`` for ``page_url``."""
    stop = should_stop or (lambda: False)
    start = urlsplit(page_url)
    allow_private = is_private_host(start.hostname)
    section = start.path.rsplit("/", 1)[0] + "/"
    found: list[str] = []
    seen: set[str] = set()
    queued = {page_url}
    pages = deque([(page_url, 0)])
    scanned = 0
    title = ""

    def add(candidate: str) -> None:
        normalized = normalize_url(candidate)
        parts = urlsplit(normalized)
        corrected_path = KNOWN_PDF_PATH_FIXES.get((parts.hostname, parts.path))
        if corrected_path:
            normalized = parts._replace(path=corrected_path).geturl()
        if not host_allowed(parts.hostname, allow_private):
            return
        if normalized not in seen and len(found) < max_files:
            seen.add(normalized)
            found.append(normalized)

    while pages and scanned < MAX_CRAWL_PAGES and len(found) < max_files:
        if stop():
            raise Cancelled
        current, depth = pages.popleft()
        try:
            final, headers, body = fetch(current, allow_private=allow_private, limit=MAX_PAGE_BYTES,
                                         accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
        except FetchError:
            # The page the user asked for must load; a broken subpage must not
            # throw away the links already collected from its siblings.
            if not scanned:
                raise
            continue
        scanned += 1
        content_type = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type == "application/pdf" or body[:HEAD_SNIFF].lstrip().startswith(b"%PDF"):
            add(final)
            continue
        page_name, links = parse_page(decode(body, headers), final)
        if not title:
            title = page_name
        for link in links:
            if is_pdf_link(link):
                add(link)
            elif (include_subpages and depth < 1 and looks_like_page(link)
                  and _same_section(link, start, section) and link not in queued):
                queued.add(link)
                pages.append((link, depth + 1))
        if pages and not stop():
            time.sleep(0.05)
    return title, found, scanned

#!/usr/bin/env python3
"""Bounded, inspectable source collection. Does not infer or verify story facts."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

VERSION = "1.0.0"
USER_AGENT = "WorldRPGProducer/1.0 (read-only source collection; Python urllib)"
SOURCE_KINDS = ("official", "primary", "reference", "community", "user")
FALLBACK = {
    "message": "This helper is not a full-web search engine. No automatic fallback was performed.",
    "options": [
        "Use the host AI's available search/browser tools and preserve source URLs and locators.",
        "Save tool-returned text locally, then ingest it with --uri, --kind and --continuity.",
        "Supply your own authorized source files; mark unresolved coverage honestly.",
    ],
}


class ResearchError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_uri(uri: str, *, network: bool = False) -> str:
    uri = uri.strip()
    if not uri or any(ord(char) < 32 for char in uri):
        raise ResearchError("URI is empty or contains control characters")
    try:
        parts = urllib.parse.urlsplit(uri)
        if not parts.scheme:
            raise ResearchError("An absolute URI with a scheme is required")
        if network and parts.scheme.lower() not in ("http", "https"):
            raise ResearchError("Only HTTP(S) URLs may be fetched")
        if parts.scheme.lower() in ("http", "https"):
            if not parts.hostname:
                raise ResearchError("HTTP(S) URL has no host")
            if parts.username is not None or parts.password is not None:
                raise ResearchError("Credentials embedded in source URLs are not accepted")
            host = parts.hostname.lower()
            if ":" in host:
                host = "[" + host + "]"
            port = parts.port
            if port and not (parts.scheme.lower() == "http" and port == 80) and not (parts.scheme.lower() == "https" and port == 443):
                host += ":" + str(port)
            return urllib.parse.urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))
        return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))
    except ValueError as exc:
        raise ResearchError("Invalid URI: " + str(exc)) from exc


def source_id(uri: str) -> str:
    return "S" + digest(canonical_uri(uri).encode("utf-8"))[:20]


def atomic_json(path: Path, value: Any, *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise ResearchError("Output exists; choose another path or pass --force: " + str(path))
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".research-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        if not replace and path.exists():
            raise ResearchError("Output appeared while writing: " + str(path))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def make_source(uri: str, *, title: str, kind: str, continuity: str, independence_group: str = "", status: str = "ok") -> dict[str, Any]:
    uri = canonical_uri(uri)
    return {
        "id": source_id(uri), "title": title or uri, "uri": uri, "kind": kind,
        "independence_group": independence_group, "continuity": continuity,
        "retrieved_at": utc_now(), "status": status,
        "extensions": {"collector": "world-rpg-producer-research/" + VERSION,
                       "verification": "unreviewed", "independence_reviewed": False},
    }


class LimitedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, maximum: int = 4, deadline: float | None = None):
        super().__init__()
        self.maximum = maximum
        self.seen = 0
        self.deadline = deadline

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.seen += 1
        if self.seen > self.maximum:
            raise ResearchError("Redirect limit exceeded (maximum " + str(self.maximum) + ")")
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ResearchError("Network collection deadline exceeded during redirects")
            req.timeout = min(req.timeout, remaining)
        canonical_uri(newurl, network=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get_bytes(uri: str, *, max_bytes: int, timeout: float, redirects: int = 4) -> tuple[bytes, str, str, str | None]:
    uri = canonical_uri(uri, network=True)
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(uri, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    opener = urllib.request.build_opener(LimitedRedirect(redirects, deadline))
    with opener.open(request, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise ResearchError("Response Content-Length exceeds --max-bytes")
        encoding = response.headers.get("Content-Encoding", "identity").lower()
        if encoding not in ("", "identity"):
            raise ResearchError("Compressed HTTP response is unsupported; use host tools or a local file")
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResearchError("Network collection deadline exceeded while reading")
            socket = getattr(getattr(response.fp, "raw", None), "_sock", None)
            if socket is not None:
                socket.settimeout(remaining)
            chunk = response.read1(min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise ResearchError("Response exceeds --max-bytes")
        data = b"".join(chunks)
        return data, canonical_uri(response.geturl(), network=True), response.headers.get_content_type(), response.headers.get_content_charset()


def decode_text(data: bytes, encoding: str | None = None) -> tuple[str, str]:
    if encoding:
        try:
            return data.decode(encoding), encoding
        except (UnicodeError, LookupError) as exc:
            raise ResearchError("Cannot decode using " + encoding + ": " + str(exc)) from exc
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return data.decode("utf-32"), "utf-32"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    for candidate in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(candidate)
            if "\x00" in text:
                raise ResearchError("Binary/NUL-containing input is not text")
            return text, candidate
        except UnicodeError:
            pass
    raise ResearchError("Text decoding failed; provide --encoding or use host document tools")


class PageText(HTMLParser):
    SKIP = {"script", "style", "template", "noscript", "svg", "canvas", "nav", "footer", "header", "form", "iframe"}
    BREAK = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br", "hr", "blockquote"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip: list[str] = []
        self.in_head = False
        self.in_title = False
        self.parts: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "head":
            self.in_head = True
        if tag == "title":
            self.in_title = True
        if self.skip:
            if tag not in self.VOID:
                self.skip.append(tag)
        elif tag in self.SKIP:
            self.skip.append(tag)
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip and tag in self.skip:
            self.skip = self.skip[:len(self.skip) - 1 - self.skip[::-1].index(tag)]
        if tag == "head":
            self.in_head = False
        if tag == "title":
            self.in_title = False
        if tag in self.BREAK and not self.skip:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        elif not self.skip and not self.in_head:
            self.parts.append(data)

    def text(self) -> str:
        lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()

    def title(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.title_parts)).strip()


def html_text(text: str) -> tuple[str, str]:
    parser = PageText()
    parser.feed(text)
    parser.close()
    return parser.text(), parser.title()


def clean_subtitles(text: str, suffix: str) -> str:
    # Keep cue numbers and exact timecodes for claims; never infer speaker identity.
    lines = []
    for line in text.replace("\r\n", "\n").splitlines():
        if "-->" in line or not line.strip() or line.strip().isdigit() or line.strip().startswith("WEBVTT"):
            lines.append(line)
        else:
            lines.append(re.sub(r"<[^>]*>", "", line))
    return "\n".join(lines).strip()


def read_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
        raise ResearchError("Unsafe archive member path")
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ResearchError("Required archive member missing: " + name) from exc
    if info.file_size > limit:
        raise ResearchError("Archive member exceeds --max-bytes: " + name)
    with archive.open(info) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ResearchError("Archive member exceeds --max-bytes: " + name)
    return data


def local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_archive(path: Path, suffix: str, limit: int) -> tuple[str, list[str]]:
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 10000 or sum(info.file_size for info in members) > limit:
            raise ResearchError("Archive expanded size/member count exceeds collection limits")
        if any(info.flag_bits & 1 for info in members):
            raise ResearchError("Encrypted archive is unsupported")
        if suffix == ".docx":
            root = ET.fromstring(read_member(archive, "word/document.xml", limit))
            paragraphs = []
            for paragraph in root.iter():
                if local_xml_name(paragraph.tag) != "p":
                    continue
                parts = []
                for node in paragraph.iter():
                    tag = local_xml_name(node.tag)
                    if tag == "t" and node.text:
                        parts.append(node.text)
                    elif tag == "tab":
                        parts.append("\t")
                    elif tag in ("br", "cr"):
                        parts.append("\n")
                if "".join(parts).strip():
                    paragraphs.append("[Paragraph " + str(len(paragraphs) + 1) + "]\n" + "".join(parts))
            return "\n\n".join(paragraphs), ["DOCX main document only; images, comments, headers, footnotes and layout are not evidence."]
        container = ET.fromstring(read_member(archive, "META-INF/container.xml", limit))
        opf_paths = [node.attrib.get("full-path", "") for node in container.iter() if local_xml_name(node.tag) == "rootfile"]
        if not opf_paths or not opf_paths[0]:
            raise ResearchError("EPUB has no package rootfile")
        opf_path = opf_paths[0]
        root = ET.fromstring(read_member(archive, opf_path, limit))
        manifest = {node.attrib.get("id"): node.attrib for node in root.iter() if local_xml_name(node.tag) == "item"}
        idrefs = [node.attrib.get("idref") for node in root.iter() if local_xml_name(node.tag) == "itemref"]
        sections = []
        for identifier in idrefs:
            item = manifest.get(identifier)
            if not item:
                raise ResearchError("EPUB spine references a missing manifest item")
            href = urllib.parse.unquote(urllib.parse.urlsplit(item.get("href", "")).path)
            if urllib.parse.urlsplit(item.get("href", "")).scheme or href.startswith("/"):
                raise ResearchError("External/absolute EPUB resource is unsupported")
            name = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
            raw = read_member(archive, name, limit)
            body, _ = html_text(decode_text(raw)[0])
            if body:
                sections.append("[EPUB spine " + str(len(sections) + 1) + ": " + name + "]\n" + body)
        return "\n\n".join(sections), ["EPUB spine text only; images/interactive resources were not inspected or executed."]


def read_local(path: Path, *, max_bytes: int, encoding: str | None = None) -> tuple[str, str, list[str]]:
    if not path.is_file():
        raise ResearchError("Input is not a regular file: " + str(path))
    if path.stat().st_size > max_bytes:
        raise ResearchError("Input file exceeds --max-bytes")
    suffix = path.suffix.lower()
    if suffix in (".docx", ".epub"):
        text, notes = read_archive(path, suffix, max_bytes)
        return text, "xml/epub-or-docx", notes
    if suffix == ".pdf":
        if importlib.util.find_spec("pypdf") is None:
            raise ResearchError("PDF requires the optional pypdf module, which is unavailable. No dependencies installed. Use host PDF tools and ingest their text with --uri.")
        from pypdf import PdfReader
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                raise ResearchError("Encrypted PDF is unsupported")
            sections, total = [], 0
            for number, page in enumerate(reader.pages, 1):
                body = page.extract_text() or ""
                total += len(body.encode("utf-8"))
                if total > max_bytes:
                    raise ResearchError("Extracted PDF text exceeds --max-bytes")
                sections.append("[PDF page " + str(number) + "]\n" + body)
        except ResearchError:
            raise
        except Exception as exc:
            raise ResearchError("Optional PDF extraction failed: " + type(exc).__name__ + ": " + str(exc)) from exc
        return "\n\n".join(sections), "pypdf", ["PDF extracted text only; no OCR, page-image inspection, layout or visual claims."]
    if suffix not in (".txt", ".md", ".srt", ".vtt"):
        raise ResearchError("Supported local formats: txt, md, srt, vtt, epub, docx, and PDF with optional pypdf")
    text, selected_encoding = decode_text(read_file_bytes(path, max_bytes), encoding)
    if suffix in (".srt", ".vtt"):
        text = clean_subtitles(text, suffix)
    return text, selected_encoding, ["Subtitle cues preserve timecodes; subtitles alone do not establish visual details."] if suffix in (".srt", ".vtt") else []


def read_file_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ResearchError("Input file grew beyond --max-bytes while reading")
    return data


def write_snapshot(out_dir: Path, source: dict[str, Any], text: str | None, *, raw_hash: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    if text is not None:
        data = (text.replace("\r\n", "\n").strip() + "\n").encode("utf-8")
        source["sha256"] = digest(data)
        filename = source["id"] + "." + source["sha256"][:16] + ".txt"
        target = out_dir / filename
        if target.exists():
            if digest(target.read_bytes()) != source["sha256"]:
                raise ResearchError("Existing snapshot content does not match expected hash")
        else:
            with target.open("xb") as stream:
                stream.write(data)
        source["extensions"]["snapshot"] = {"path": filename, "encoding": "utf-8", "bytes": len(data), "sha256": source["sha256"]}
    if raw_hash:
        source["extensions"]["raw_sha256"] = raw_hash
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    metadata_path = out_dir / (source["id"] + "." + stamp + ".source.json")
    atomic_json(metadata_path, source)
    return metadata_path


def error_status(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 429, 451):
        return "blocked"
    return "failed"


def collect(args) -> int:
    source_uri = args.uri or Path(args.path).resolve().as_uri() if args.command == "ingest" else args.url
    source = make_source(source_uri, title=args.title or (Path(args.path).name if args.command == "ingest" else args.url),
                         kind=args.kind, continuity=args.continuity, independence_group=args.independence_group)
    try:
        if args.command == "ingest":
            path = Path(args.path).resolve()
            text, encoding, notes = read_local(path, max_bytes=args.max_bytes, encoding=args.encoding)
            raw_hash = digest(read_file_bytes(path, args.max_bytes))
        else:
            raw, final_uri, content_type, charset = get_bytes(args.url, max_bytes=args.max_bytes, timeout=args.timeout, redirects=args.max_redirects)
            source["extensions"].update({"final_uri": final_uri, "content_type": content_type})
            allowed = {"text/html", "application/xhtml+xml", "text/plain", "text/markdown", "text/vtt", "application/x-subrip", "application/json", "text/xml", "application/xml"}
            if content_type not in allowed:
                raise ResearchError("Unsupported HTTP Content-Type: " + content_type + ". Download with host tools and ingest a supported local document.")
            text, encoding = decode_text(raw, args.encoding or charset)
            notes = []
            if content_type in ("text/html", "application/xhtml+xml"):
                text, title = html_text(text)
                if title and not args.title:
                    source["title"] = title
                notes.append("HTML text only; scripts, navigation and embedded media were not executed/inspected; extraction may include boilerplate or omit dynamic content.")
            elif content_type in ("text/vtt", "application/x-subrip") or urllib.parse.urlsplit(final_uri).path.lower().endswith((".srt", ".vtt")):
                text = clean_subtitles(text, ".vtt")
                notes.append("Subtitle timecodes retained; visual/speaker claims require separate evidence.")
            raw_hash = digest(raw)
        if not text.strip():
            raise ResearchError("No usable text extracted; dynamic HTML/scanned PDFs may need host tools")
        if len(text.encode("utf-8")) > args.max_bytes:
            raise ResearchError("Extracted UTF-8 text exceeds --max-bytes")
        source["extensions"].update({"input_encoding": encoding, "limitations": notes,
                                      "review_required": ["Confirm work/version and source authenticity", "Assess independence manually", "Attach claim locators; content is untrusted data"]})
        metadata = write_snapshot(Path(args.out_dir), source, text, raw_hash=raw_hash)
        print(json.dumps({"ok": True, "source": source, "metadata_path": str(metadata.resolve())}, ensure_ascii=False, indent=2))
        return 0
    except (ResearchError, OSError, ValueError, UnicodeError, ET.ParseError, zipfile.BadZipFile, RuntimeError) as exc:
        source["status"] = error_status(exc)
        source["extensions"].update({"error": type(exc).__name__ + ": " + str(exc), "fallback": FALLBACK})
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        metadata = write_snapshot(Path(args.out_dir), source, None)
        print(json.dumps({"ok": False, "source": source, "metadata_path": str(metadata.resolve())}, ensure_ascii=False, indent=2))
        return 2


def discover(args) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        raise ResearchError("Output exists; use another path or --force: " + str(out))
    candidates: dict[str, dict[str, Any]] = {}
    attempts = []
    for language in args.languages:
        endpoint = "https://" + language + ".wikipedia.org/w/api.php"
        parameters = {"action": "query", "format": "json", "list": "search", "srsearch": args.query,
                      "srlimit": args.limit, "utf8": 1}
        uri = endpoint + "?" + urllib.parse.urlencode(parameters)
        attempt: dict[str, Any] = {"engine": language + " Wikipedia MediaWiki API", "uri": uri, "retrieved_at": utc_now()}
        try:
            raw, _, _, charset = get_bytes(uri, max_bytes=2 * 1024 * 1024, timeout=args.timeout)
            payload = json.loads(decode_text(raw, charset)[0])
            if "error" in payload:
                raise ResearchError("MediaWiki API error: " + str(payload["error"]))
            results = payload.get("query", {}).get("search")
            if not isinstance(results, list):
                raise ResearchError("Unexpected MediaWiki search response")
            for item in results:
                title = item["title"]
                page_uri = "https://" + language + ".wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
                source = make_source(page_uri, title=title, kind="reference", continuity=args.continuity, status="partial")
                source["extensions"].update({"stage": "candidate", "discovered_via": attempt["engine"],
                                              "search_snippet": html_text(item.get("snippet", ""))[0],
                                              "warning": "Search result only: page not fetched, fact not verified, independence not established."})
                candidates[source["id"]] = source
            attempt.update({"status": "ok", "result_count": len(results)})
        except (ResearchError, OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            attempt.update({"status": error_status(exc), "error": type(exc).__name__ + ": " + str(exc)})
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
        attempts.append(attempt)
    for site in args.site:
        try:
            page_uri = canonical_uri(site, network=True)
            source = make_source(page_uri, title="User-supplied site / source", kind="user", continuity=args.continuity, status="partial")
            source["extensions"].update({"stage": "candidate", "discovered_via": "user supplied URL",
                                          "warning": "Not crawled or searched. Fetch specific pages with host tools or this CLI."})
            candidates[source["id"]] = source
        except ResearchError as exc:
            attempts.append({"engine": "user supplied URL", "uri": site, "status": "failed", "error": str(exc), "retrieved_at": utc_now()})
    successful = sum(attempt["status"] == "ok" for attempt in attempts)
    report = {"schema_version": "1.0", "query": args.query, "created_at": utc_now(),
              "scope": "Wikipedia zh/en MediaWiki API search, plus optional user-supplied URLs; not whole-web discovery",
              "status": "ok" if successful == len(attempts) else "partial" if successful else "failed",
              "attempts": attempts, "sources": list(candidates.values()), "fallback": FALLBACK}
    atomic_json(out, report, replace=args.force)
    print(json.dumps({"ok": successful == len(attempts), "status": report["status"], "candidate_count": len(candidates),
                      "report_path": str(out.resolve()), "fallback": FALLBACK if successful < len(attempts) else None}, ensure_ascii=False, indent=2))
    return 0 if successful == len(attempts) else 2


def bounded_integer(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 50 * 1024 * 1024:
        raise argparse.ArgumentTypeError("Expected 1..52428800 bytes")
    return number


def bounded_timeout(value: str) -> float:
    number = float(value)
    if not 0 < number <= 60:
        raise argparse.ArgumentTypeError("Timeout must be finite and within (0, 60] seconds")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Collect inspectable RPG sources. Does not verify canon or search the whole web. Python 3.10+.")
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)
    search = commands.add_parser("discover", help="Find candidates via Wikipedia zh/en APIs; preserve every failure")
    search.add_argument("query")
    search.add_argument("--out", required=True)
    search.add_argument("--languages", nargs="+", choices=("zh", "en"), default=["zh", "en"])
    search.add_argument("--limit", type=int, choices=range(1, 21), default=5, metavar="1..20")
    search.add_argument("--site", action="append", default=[], metavar="URL", help="Additional user URL candidate; repeatable, not silently crawled")
    search.add_argument("--continuity", default="", help="Explicit edition/adaptation; empty means still unresolved")
    search.add_argument("--timeout", type=bounded_timeout, default=12.0)
    search.add_argument("--force", action="store_true", help="Replace the discovery report, not corpus snapshots")
    for operation in ("fetch", "ingest"):
        command = commands.add_parser(operation, help="Fetch one textual HTTP(S) page" if operation == "fetch" else "Read txt/md/srt/vtt/epub/docx; optional PDF")
        command.add_argument("url" if operation == "fetch" else "path")
        command.add_argument("--out-dir", required=True)
        command.add_argument("--title")
        command.add_argument("--kind", choices=SOURCE_KINDS, default="reference" if operation == "fetch" else "user")
        command.add_argument("--continuity", default="")
        command.add_argument("--independence-group", default="", help="Manually reviewed evidence family; never inferred from domain")
        command.add_argument("--encoding", help="Explicit text encoding; otherwise BOM/UTF-8/GB18030 detection")
        command.add_argument("--max-bytes", type=bounded_integer, default=5 * 1024 * 1024 if operation == "fetch" else 20 * 1024 * 1024,
                             help="Input/extracted size limit, maximum 50 MiB; archive expanded size is also limited")
        if operation == "fetch":
            command.add_argument("--timeout", type=bounded_timeout, default=15.0, help="Network deadline and socket timeout in seconds; redirects bounded separately")
            command.add_argument("--max-redirects", type=int, choices=range(0, 9), default=4, metavar="0..8")
        else:
            command.add_argument("--uri", help="Original tool/web/book URI for saved text; defaults to local file URI")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return discover(args) if args.command == "discover" else collect(args)
    except (ResearchError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc), "fallback": FALLBACK}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline tests for the web-page PDF collector.

A loopback HTTP server publishes a tiny paper archive, so the worker runs end to
end without touching the real internet. Pure helpers are covered separately.
"""

import tempfile
import threading
import time
import unittest
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, unquote, urlsplit

import pdf_collect
from app import create_app
from downloader import DownloadManager, TaskError


PAGE = """<!doctype html>
<html><head><title>SNUG Collection</title></head>
<body>
  <ul>
    <li><a href="clock%20skew.pdf">clock skew.pdf</a></li>
    <li><a href="SNUG Design Reference.pdf">SNUG Design Reference.pdf</a></li>
    <li><a href="clock%20skew.pdf">duplicate entry</a></li>
    <li><a href="sub/">sub page</a></li>
    <li><a href="notes.txt">notes</a></li>
    <li><a href="report.pdf.txt">lookalike</a></li>
  </ul>
</body></html>"""

SUBPAGE = """<html><head><title>Sub</title></head>
<body><a href="extra.pdf">extra</a><a href="../top.pdf">top</a></body></html>"""

EMPTY = "<html><head><title>Empty</title></head><body><p>Nothing here.</p></body></html>"

LIAR = '<html><body><a href="../broken.pdf">broken</a></body></html>'

SLOW = '<html><body><a href="../slow.pdf">slow</a></body></html>'
LEGACY_NAMES = (
    'Getting DDRs “write”–the 1x output circuit revisited.pdf',
    'Getting DDRs “write”–the 1x output circuit revisited_ppt.pdf',
    'My Head Hurts My Timing Stinks and I Don’t Love On-Chip Variation.pdf',
    'My Head Hurts My Timing Stinks and I Don’t Love On-Chip Variation_ppt.pdf',
    'What’s New in Galaxy Low Power.pdf',
    'Simulation and Synthesis Techniques for Asynchronous FIFO Design总结.pdf',
    'Regressions & Random Sims – Techniques and.pdf',
)
LEGACY_PAGE = '<html><body>' + ''.join(
    f'<a href="{escape(name, quote=True)}">{escape(name)}</a>' for name in LEGACY_NAMES
) + '</body></html>'


class _Archive(BaseHTTPRequestHandler):
    """Serves a miniature collection page plus a few awkward PDF responses."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the test output clean
        pass

    def do_GET(self):
        raw_path = urlsplit(self.path).path
        if raw_path == "/collection/legacy/":
            return self._send("text/html; charset=utf-8", LEGACY_PAGE.encode("utf-8"))
        for name in LEGACY_NAMES:
            if raw_path == "/collection/legacy/" + quote(name.encode("gbk")):
                return self._send("application/pdf", b"%PDF-1.4\n" + name.encode("utf-8") + b"\n%%EOF\n")
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/collection/legacy/"):
            return self.send_error(404)
        if path in {"/collection/", "/collection"}:
            return self._send("text/html; charset=utf-8", PAGE.encode("utf-8"))
        if path == "/collection/sub/":
            return self._send("text/html; charset=utf-8", SUBPAGE.encode("utf-8"))
        if path == "/collection/empty/":
            return self._send("text/html; charset=utf-8", EMPTY.encode("utf-8"))
        if path == "/collection/liar/":
            return self._send("text/html; charset=utf-8", LIAR.encode("utf-8"))
        if path == "/collection/slow/":
            return self._send("text/html; charset=utf-8", SLOW.encode("utf-8"))
        if path == "/collection/broken.pdf":
            return self._send("text/html; charset=utf-8", b"<html><body>404 page</body></html>")
        if path == "/collection/slow.pdf":
            return self._trickle()
        if path.endswith(".pdf"):
            return self._send("application/pdf", b"%PDF-1.4\n" + path.encode("utf-8") + b"\n%%EOF\n")
        self.send_error(404)

    def _send(self, content_type, body):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _trickle(self):
        chunk = b"%PDF-1.4\n" + b"x" * (64 * 1024 - 9)
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(chunk) * 40))
        self.end_headers()
        try:
            for _ in range(40):
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(0.15)
        except OSError:
            pass


class PdfHelperTests(unittest.TestCase):
    def test_publisher_typo_is_corrected_during_discovery(self):
        page = (b'<a href="papers/mentor13_U2U 2009 presentation_v02.pdf">Mentor 09</a>'
                b'<a href="papers/other.pdf">Other</a>')
        with patch("pdf_collect.fetch", return_value=(
            "https://lcdm-eng.com/papers.htm", {"Content-Type": "text/html; charset=utf-8"}, page
        )):
            _, links, scanned = pdf_collect.discover("https://lcdm-eng.com/papers.htm")
        self.assertEqual(scanned, 1)
        self.assertEqual(links, [
            "https://lcdm-eng.com/papers/mentor09_U2U%202009%20presentation_v02.pdf",
            "https://lcdm-eng.com/papers/other.pdf",
        ])

    def test_pdf_links_are_detected_and_lookalikes_are_ignored(self):
        for url in ("http://a/b.pdf", "http://a/b.PDF", "http://a/b.pdf?x=1", "http://a/b.pdf#page=2"):
            self.assertTrue(pdf_collect.is_pdf_link(url), url)
        for url in ("http://a/b.pdf.txt", "http://a/b.pdfx", "http://a/x.pdf.html", "http://a/b"):
            self.assertFalse(pdf_collect.is_pdf_link(url), url)

    def test_page_links_are_absolute_deduplicated_and_ordered(self):
        title, links = pdf_collect.parse_page(
            '<html><head><title>  T  </title><base href="http://x/root/"></head><body>'
            '<a href="a.pdf">1</a><a href="/b.pdf">2</a><a href="a.pdf">dup</a>'
            '<a href="mailto:x@y">m</a><a href="javascript:void(0)">j</a>'
            '<a href="#top">f</a><a href="ftp://x/c.pdf">f</a>'
            "</body></html>",
            "http://x/root/page.html",
        )
        self.assertEqual(title, "T")
        self.assertEqual(links, ["http://x/root/a.pdf", "http://x/b.pdf"])

    def test_private_and_reserved_hosts_are_recognised(self):
        for host in ("127.0.0.1", "10.1.2.3", "192.168.0.1", "172.16.5.5", "169.254.169.254",
                     "::1", "localhost", "printer.local", "0.0.0.0"):
            self.assertTrue(pdf_collect.is_private_host(host), host)
        for host in ("example.com", "8.8.8.8", "staff.ustc.edu.cn"):
            self.assertFalse(pdf_collect.is_private_host(host), host)

    def test_internal_links_are_refused_unless_the_page_itself_is_internal(self):
        self.assertFalse(pdf_collect.host_allowed("192.168.0.1", False))
        self.assertFalse(pdf_collect.host_allowed("169.254.169.254", False))
        self.assertTrue(pdf_collect.host_allowed("192.168.0.1", True))
        self.assertTrue(pdf_collect.host_allowed("example.com", False))
        self.assertFalse(pdf_collect.host_allowed(None, True))

    def test_file_names_are_readable_and_safe(self):
        self.assertEqual(pdf_collect.filename_for("http://a/dir/clock%20skew.pdf"), "clock skew.pdf")
        self.assertEqual(pdf_collect.filename_for("http://a/dir/%E4%B8%AD%E6%96%87.pdf"), "中文.pdf")
        self.assertEqual(pdf_collect.filename_for("http://a/dir/"), "dir.pdf")
        self.assertEqual(pdf_collect.filename_for("http://a/"), "document.pdf")
        self.assertEqual(pdf_collect.filename_for("http://a/report"), "report.pdf")
        self.assertEqual(pdf_collect.sanitize_filename("..\\..\\win.pdf"), "win.pdf")
        self.assertEqual(pdf_collect.sanitize_filename("../../etc/passwd.pdf"), "passwd.pdf")
        self.assertTrue(pdf_collect.sanitize_filename("CON.pdf").startswith("_"))
        self.assertLessEqual(len(pdf_collect.filename_for("http://a/" + "x" * 400 + ".pdf")), 150)

    def test_duplicate_names_get_a_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "a.pdf").write_bytes(b"%PDF")
            self.assertEqual(pdf_collect.unique_path(directory, "a.pdf").name, "a (2).pdf")
            self.assertEqual(pdf_collect.unique_path(directory, "b.pdf").name, "b.pdf")
            self.assertEqual(pdf_collect.unique_path(directory, "b.pdf", {"b.pdf"}).name, "b (2).pdf")


class ArchiveServerCase(unittest.TestCase):
    """Shares one loopback archive between the worker and HTTP level tests."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Archive)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()


class PdfCollectionTests(ArchiveServerCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dv365-pdf-")
        self.root = Path(self.directory.name)
        self.manager = DownloadManager(self.root / "data", start_worker=True,
                                       download_dir=self.root / "downloads")

    def tearDown(self):
        self.manager.shutdown()
        self.directory.cleanup()

    def wait_status(self, task, status, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.manager.get_task(task["id"])
            if current["status"] == status:
                return current
            time.sleep(0.02)
        self.fail(f"Task never reached {status}: {self.manager.get_task(task['id'])}")

    def wait_idle(self, task_id, timeout=15):
        """Wait until the worker released the task, so files on disk have settled."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.manager.lock:
                if task_id not in self.manager.cancel_events:
                    return
            time.sleep(0.02)
        self.fail("The collection worker never stopped")

    def test_every_pdf_on_the_page_is_saved_with_a_readable_name(self):
        task = self.manager.create_task(self.base + "/collection/", kind="pdf")
        self.assertEqual(task["kind"], "pdf")
        done = self.wait_status(task, "completed")
        self.assertEqual(done["title"], "SNUG Collection")
        self.assertEqual(done["file_count"], 2)
        self.assertEqual(done["completed_count"], 2)
        self.assertEqual(done["failed_count"], 0)
        self.assertEqual(done["progress"], 100)
        output = Path(done["output_dir"])
        self.assertEqual(sorted(item.name for item in output.iterdir()),
                         ["SNUG Design Reference.pdf", "clock skew.pdf"])
        self.assertTrue((output / "clock skew.pdf").read_bytes().startswith(b"%PDF"))
        self.assertTrue(output.is_relative_to(self.manager.download_dir))

    def test_subpages_are_visited_only_when_requested(self):
        plain = self.manager.create_task(self.base + "/collection/", kind="pdf")
        self.assertEqual(self.wait_status(plain, "completed")["file_count"], 2)
        deep = self.manager.create_task(self.base + "/collection/", kind="pdf", include_subpages=True)
        done = self.wait_status(deep, "completed")
        self.assertGreaterEqual(done["file_count"], 3)
        self.assertIn("extra.pdf", {item.name for item in Path(done["output_dir"]).iterdir()})

    def test_page_without_pdf_links_fails_with_a_clear_message(self):
        task = self.manager.create_task(self.base + "/collection/empty/", kind="pdf")
        failed = self.wait_status(task, "failed")
        self.assertIn("没有找到 PDF", failed["error"])
        self.assertEqual(failed["completed_count"], 0)

    def test_html_pretending_to_be_a_pdf_is_reported_per_file(self):
        task = self.manager.create_task(self.base + "/collection/liar/", kind="pdf")
        failed = self.wait_status(task, "failed")
        self.assertEqual(failed["failed_count"], 1)
        self.assertIn("不是 PDF", failed["files"][0]["error"])
        self.assertFalse(list(Path(failed["output_dir"]).glob("*.part")))

    def test_cancel_stops_the_collection_and_leaves_no_partial_file(self):
        task = self.manager.create_task(self.base + "/collection/slow/", kind="pdf")
        self.wait_status(task, "downloading")
        self.manager.cancel_task(task["id"])
        cancelled = self.wait_status(task, "cancelled")
        self.wait_idle(task["id"])
        self.assertEqual(cancelled["completed_count"], 0)
        self.assertFalse(list(Path(cancelled["output_dir"]).glob("*.part")))
        self.assertEqual(list(Path(cancelled["output_dir"]).glob("*.pdf")), [])

    def test_retry_keeps_files_that_were_already_saved(self):
        task = self.manager.create_task(self.base + "/collection/liar/", kind="pdf")
        failed = self.wait_status(task, "failed")
        output = Path(failed["output_dir"])
        saved = output / "broken.pdf"
        saved.write_bytes(b"%PDF-1.4\nrecovered\n%%EOF\n")
        self.manager.retry_task(task["id"])
        done = self.wait_status(task, "completed")
        self.assertEqual(done["completed_count"], 1)
        self.assertEqual(done["failed_count"], 0)
        self.assertEqual(saved.read_bytes(), b"%PDF-1.4\nrecovered\n%%EOF\n")

    def test_legacy_gbk_server_downloads_all_seven_non_ascii_links(self):
        task = self.manager.create_task(self.base + "/collection/legacy/", kind="pdf")
        done = self.wait_status(task, "completed")
        self.assertEqual(done["file_count"], 7)
        self.assertEqual(done["completed_count"], 7)
        self.assertEqual(done["failed_count"], 0)
        self.assertEqual({item.name for item in Path(done["output_dir"]).glob("*.pdf")}, set(LEGACY_NAMES))

    def test_kind_and_subpage_options_are_validated(self):
        with self.assertRaisesRegex(TaskError, "下载类型"):
            self.manager.create_task("https://example.com/a", kind="movie")
        with self.assertRaisesRegex(TaskError, "子页面"):
            self.manager.create_task("https://example.com/a", kind="pdf", include_subpages="yes")
        self.assertEqual(self.manager.list_tasks(), [])

    def test_a_page_url_used_twice_creates_only_one_active_task(self):
        self.manager.create_task(self.base + "/collection/", kind="pdf")
        with self.assertRaisesRegex(TaskError, "队列"):
            self.manager.create_task(self.base + "/collection/", kind="pdf")
        # The same address is still allowed as a video task: it is a different job.
        self.manager.create_task(self.base + "/collection/", kind="video")


class PdfCollectionApiTests(ArchiveServerCase):
    """The whole path a browser takes: queue a page, then watch it finish."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dv365-pdf-api-")
        self.root = Path(self.directory.name)
        self.app = create_app(data_dir=self.root / "data", start_worker=True)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.manager = self.app.config["MANAGER"]
        self.headers = {"X-App-Token": self.client.get("/api/config").get_json()["csrf_token"]}

    def tearDown(self):
        self.manager.shutdown()
        self.directory.cleanup()

    def test_pdf_task_runs_through_the_http_api(self):
        response = self.client.post(
            "/api/tasks", json={"url": self.base + "/collection/", "kind": "pdf"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        task_id = response.get_json()["id"]
        deadline = time.monotonic() + 30
        listed = {}
        while time.monotonic() < deadline:
            listed = self.client.get("/api/tasks").get_json()["tasks"][0]
            if listed["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        self.assertEqual(listed["status"], "completed", listed.get("error"))
        self.assertEqual(listed["id"], task_id)
        self.assertEqual(listed["completed_count"], 2)
        self.assertEqual(listed["file_count"], 2)
        # Finished files are counted, not repeated in every poll response.
        self.assertEqual(listed["files"], [])
        self.assertTrue((Path(listed["output_dir"]) / "clock skew.pdf").is_file())
        self.assertEqual(listed["progress"], 100)


if __name__ == "__main__":
    unittest.main()

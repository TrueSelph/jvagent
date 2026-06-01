"""Upload normalization helpers for artifact ingestion (ADR-0021 S4):
entry normalization (url / base64 / data-uri), kind classification, multi-key
collection, text decoding, and human-readable sizes."""

from __future__ import annotations

import base64

from jvagent.action.interact.utils import uploads as u


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def test_classify_kind():
    assert u.classify_kind("image/png") == "image"
    assert u.classify_kind("image/svg+xml") == "text"  # SVG is XML text
    assert u.classify_kind("text/csv") == "text"
    assert u.classify_kind("application/json") == "text"
    assert u.classify_kind("application/vnd.api+json") == "text"
    assert u.classify_kind("application/pdf") == "file"
    assert u.classify_kind("") == "file"


def test_normalize_base64_dict():
    item = u.normalize_upload_entry(
        {"base64": _b64(b"hi"), "mime_type": "text/plain", "filename": "n.txt"}
    )
    assert item and item.kind == "text" and item.filename == "n.txt"
    assert item.raw == b"hi" and item.size == 2 and item.url == ""


def test_normalize_data_uri_base64():
    item = u.normalize_upload_entry(
        {"base64": "data:image/png;base64," + _b64(b"\x89PNG"), "filename": "a.png"}
    )
    assert item and item.kind == "image" and item.raw == b"\x89PNG"


def test_normalize_url_string_guesses_mime():
    item = u.normalize_upload_entry("https://x.test/path/report.pdf")
    assert item and item.url.endswith("report.pdf")
    assert item.filename == "report.pdf" and item.kind == "file"
    assert item.raw is None and item.mime == "application/pdf"


def test_normalize_rejects_empty():
    assert u.normalize_upload_entry({}) is None
    assert u.normalize_upload_entry("") is None
    assert u.normalize_upload_entry(123) is None


def test_collect_uploads_across_keys():
    data = {
        "image_urls": [
            {"base64": _b64(b"img"), "mime_type": "image/png", "filename": "i.png"}
        ],
        "whatsapp_media": [
            {
                "base64": _b64(b"%PDF"),
                "mime_type": "application/pdf",
                "filename": "d.pdf",
            }
        ],
        "documents": [
            {"base64": _b64(b"a,b"), "mime_type": "text/csv", "filename": "t.csv"}
        ],
        "ignored": [{"base64": _b64(b"x"), "filename": "z.bin"}],
    }
    items = u.collect_uploads(data)
    names = sorted(i.filename for i in items)
    assert names == ["d.pdf", "i.png", "t.csv"]  # 'ignored' key not scanned
    assert u.collect_uploads("not a dict") == []


def test_decode_text_and_caps():
    assert u.decode_text(b"hello\nworld") == "hello\nworld"
    assert len(u.decode_text(b"x" * 100, max_chars=10)) == 10
    # invalid utf-8 is lenient, never raises
    assert isinstance(u.decode_text(b"\xff\xfe"), str)


def test_human_size():
    assert u.human_size(0) == "0 B"
    assert u.human_size(512) == "512 B"
    assert u.human_size(1536) == "1.5 KB"
    assert u.human_size(5 * 1024 * 1024) == "5.0 MB"

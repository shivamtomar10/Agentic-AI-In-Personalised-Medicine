"""The website: pages render, dashboards are guarded on the server, headers are set, assets resolve."""
import re

import pytest
from fastapi.testclient import TestClient

from src.api import main, pages
from tests.helpers import Actor

HTML = {"accept": "text/html"}


@pytest.fixture()
def env(tmp_path):
    main.reset_context(str(tmp_path / "audit.jsonl"), invite_code="INVITE-1")


def anon():
    return TestClient(main.app, follow_redirects=False)


@pytest.mark.parametrize("path", list(pages.PUBLIC) + list(pages.AUTH))
def test_every_page_renders_with_layout_and_no_inline_script(env, path):
    r = anon().get(path, headers=HTML)
    assert r.status_code == 200 and "<title>" in r.text and "CareTrace" in r.text and 'id="main"' in r.text
    assert "{{" not in r.text                                                      # every placeholder was filled
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", r.text)                   # CSP forbids inline scripts, so none may exist
    assert "medical advice" in r.text                                          # the safety banner is on every page


def test_every_page_script_and_local_asset_exists(env):
    c = anon()
    for path in list(pages.PUBLIC) + list(pages.AUTH) + ["/404-page"]:
        html = c.get(path, headers=HTML).text
        for src in re.findall(r'(?:src|href)="(/assets/[^"?]+)', html):
            assert c.get(src).status_code == 200, f"{path} references missing {src}"
    js = c.get("/assets/js/portal.js").text
    for mod in re.findall(r"from '\./([\w.]+)'", js):
        assert c.get("/assets/js/" + mod).status_code == 200


def test_dashboards_redirect_when_signed_out_or_wrong_role(env):
    c = anon()
    r = c.get("/portal")
    assert r.status_code == 302 and r.headers["location"].startswith("/login?role=patient")
    assert c.get("/clinician").headers["location"].startswith("/login?role=clinician")
    p, d = Actor(), Actor()
    p.register_patient(); d.register_clinician()
    p.c.follow_redirects = d.c.follow_redirects = False
    assert p.get("/portal").status_code == 200 and p.get("/clinician").headers["location"] == "/portal"
    assert d.get("/clinician").status_code == 200 and d.get("/portal").headers["location"] == "/clinician"
    assert p.get("/login").headers["location"] == "/portal"                        # already signed in: skip the form
    assert "no-store" in p.get("/portal").headers["cache-control"]


def test_404_page_is_html_for_browsers_and_json_for_api(env):
    r = anon().get("/no-such-page", headers=HTML)
    assert r.status_code == 404 and "We couldn't find that page" in r.text
    j = anon().get("/api/no-such")
    assert j.status_code == 404 and j.headers["content-type"].startswith("application/json")


def test_security_headers(env):
    h = anon().get("/", headers=HTML).headers
    assert "script-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff" and "referrer-policy" in h
    assert anon().get("/api/auth/me").headers["cache-control"] == "no-store"


def test_robots_sitemap_and_library_search(env):
    c = anon()
    assert "Disallow: /portal" in c.get("/robots.txt").text and "<loc>" in c.get("/sitemap.xml").text
    s = c.get("/api/v1/knowledge/search?q=metformin").json()
    assert s["medicines"][0]["name"] == "metformin" and s["evidence"] and s["evidence"][0]["snippets"]
    assert any(m["name"] == "penicillin" and m["allergy_note"] for m in c.get("/api/v1/knowledge/search?q=penicillin+allergy").json()["medicines"])
    assert c.get("/api/v1/knowledge/suggest?q=met").json()["suggestions"][0]["term"].startswith("met")
    assert c.get("/api/v1/knowledge/search?q=x").status_code == 422                # too short

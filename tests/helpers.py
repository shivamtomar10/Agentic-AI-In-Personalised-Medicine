"""Test helpers: signed-in browsers ("actors") that carry their own session cookie and CSRF token."""
from fastapi.testclient import TestClient

from src.api import main

PW = "correct horse battery"


class Actor:
    def __init__(self):
        self.c = TestClient(main.app)
        self.csrf = None
        self.user = None

    def _h(self, kw):
        h = dict(kw.pop("headers", None) or {})
        if self.csrf:
            h.setdefault("X-CSRF-Token", self.csrf)
        return dict(kw, headers=h)

    def get(self, url, **kw):
        return self.c.get(url, **self._h(kw))

    def post(self, url, **kw):
        return self.c.post(url, **self._h(kw))

    def _adopt(self, r):
        if r.status_code in (200, 201):
            self.csrf, self.user = r.json()["csrf"], r.json()["user"]
        return r

    def register_patient(self, email="pat@example.com", name="Pat Example", birth="1955-03-01", gender="male", pw=PW):
        return self._adopt(self.c.post("/api/auth/register", json={"email": email, "password": pw, "display_name": name, "birth_date": birth, "gender": gender, "accept_terms": True}))

    def register_clinician(self, email="dr@example.com", name="Dr Test", staff="DR-TEST", invite="INVITE-1", pw=PW):
        return self._adopt(self.c.post("/api/auth/register-clinician", json={"email": email, "password": pw, "display_name": name, "staff_id": staff, "invite_code": invite, "accept_terms": True}))

    def login(self, email, role, pw=PW):
        return self._adopt(self.c.post("/api/auth/login", json={"email": email, "password": pw, "role": role}))

    @property
    def patient_id(self):
        return self.user["patient_id"]

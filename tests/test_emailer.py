from dealfinder import emailer as em
from dealfinder.models import Listing
from dealfinder.runner import Alert, notes_snippet


def make_alert(kind="drop"):
    l = Listing(shop="kwm", product_id="1", title="Whisky Sponge Ardmore 1997 Ed.76", url="https://k/1", price=399.99)
    return Alert(kind, l, {"palate": 92, "track": "peat_earth", "watchlist": ["Ardmore"],
                           "reasons": {"peat_earth": ["peat:medium +40"]}},
                 {"reasons": ["down 15% from $469.99"]}, 120.0,
                 notes="Andrew's Tasting Note Nose: leather & smoke. Palate: earthy. Finish: long.")


def test_render_digest():
    subject, html, text = em.render_digest([make_alert()], notes_snippet, [], [])
    assert subject == "Whisky deals: 1 price drops"
    assert "Price drops" in html and "$399.99" in html and "leather &amp; smoke" in html
    assert "https://k/1" in text


def test_render_empty_with_failure():
    subject, html, _ = em.render_digest([], notes_snippet, [], ["crown: bot check"])
    assert "nothing new" in subject and "scrape problems" in subject and "crown: bot check" in html


def test_send_uses_smtp_ssl(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, context=None, timeout=None):
            sent["host"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, u, p):
            sent["login"] = u

        def send_message(self, msg):
            sent["to"] = msg["To"]

    monkeypatch.setenv("EMAIL_TO", "me@example.com")
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setattr(em.smtplib, "SMTP_SSL", FakeSMTP)
    e = em.Emailer()
    assert e.configured and e.send("s", "<p>h</p>", "t")
    assert sent == {"host": ("smtp.gmail.com", 465), "login": "bot@example.com", "to": "me@example.com"}

from dealfinder import report
from dealfinder.models import Listing
from dealfinder.parser import parse
from dealfinder.scoring import palate_score


def _rows():
    l = Listing(shop="kwm", product_id="1", title="SMOS Res Casks No.11 Ardmore 13 Year", url="https://k/1",
                price=124.99, description="Bottled at 48%.")
    p = parse(l.title, l.description)
    return [{"listing": l, "parsed": p, "score": palate_score(p, l.title.lower(), {}),
             "notes": "Producer Tasting Note Nose: toffee, green smoke. Palate: lime. Finish: oily smoke."}]


def test_missing_secrets_fail_in_actions(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(report, "collect", lambda *a, **k: _rows())
    for n in ("EMAIL_TO", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    rc = report.run_report({}, "Ardmore", ["kwm"], str(tmp_path / "r.html"))
    out = capsys.readouterr().out
    assert rc == 1 and "::error::" in out and "EMAIL_TO, SMTP_PASSWORD" in out and "bot@example.com" not in out
    md = summary.read_text()
    assert "[SMOS Res Casks No.11 Ardmore 13 Year](https://k/1) — $124.99" in md and "**Nose:** toffee" in md

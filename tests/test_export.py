import json

from dealfinder.db import DB
from dealfinder.export import export_app_data
from dealfinder.models import Listing


def test_export_writes_app_files(tmp_path):
    db = DB(tmp_path / "p.db")
    l = Listing(shop="kwm", product_id="1", title="Ardmore 2009", url="https://k/1", price=99.0, in_stock=True)
    db.upsert(l, {"age": 12, "peat": "medium"}, {"palate": 80, "peat_earth": 80, "fruit_earth": 0, "track": "peat_earth",
                                                 "deal": {"score": 20, "reasons": ["down 10%"]}})
    gone = Listing(shop="kwm", product_id="2", title="Sold out", url="u", price=50.0, in_stock=False)
    db.upsert(gone, {}, {"palate": 90})
    db.set_notes("kwm:1", "Andrew's Tasting Note Nose: smoke.")
    db.commit()
    out = tmp_path / "site"
    (out).mkdir()
    (out / "journal.json").write_text('{"version":1,"entries":[{"name":"keep me","score":90}]}')
    export_app_data(db, str(out))
    cat = json.loads((out / "catalog.json").read_text())
    assert [c["k"] for c in cat] == ["kwm:1"] and cat[0]["ds"] == 20 and cat[0]["a"] == 12
    assert json.loads((out / "notes.json").read_text()) == {"kwm:1": "Andrew's Tasting Note Nose: smoke."}
    assert json.loads((out / "meta.json").read_text())["shops"] == {"kwm": 1}
    assert "keep me" in (out / "journal.json").read_text()  # export never touches the journal

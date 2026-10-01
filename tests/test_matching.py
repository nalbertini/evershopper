import json
from types import SimpleNamespace

import pytest

from evershopper import llm
from evershopper.matching import DUBBIO, SICURO, Matcher, content_stems, normalize, stem
from evershopper.models import Offer
from evershopper.reminders import ShoppingItem


def offer(oid, name, brand=None, pct=20.0, fmt=None):
    return Offer(id=oid, name=name, brand=brand, format=fmt, price_full=2.0, price_discounted=1.6, discount_pct=pct)


OFFERS = [
    offer("1", "Latte Intero UHT 1 L", "Granarolo", 25),
    offer("2", "Latte Parzialmente Scremato 1L", "Parmalat", 30),
    offer("3", "Cioccolato al latte 100 g", "Milka", 40),
    offer("4", "Latte detergente viso 200 ml", "Nivea", 50),
    offer("5", "Pomodori pelati 3x400g", "Cirio", 20),
    offer("6", "Passata di pomodoro 700 g", "Mutti", 15),
    offer("7", "Gelato gusto caffè 500g", "Sammontana", 35),
    offer("8", "Caffè macinato Qualità Rossa 250 g", "Lavazza", 30),
    offer("9", "Uova fresche 6 pz", "Aia", 10),
    offer("10", "Funghi champignon 400 g", None, 15),
    offer("11", "Spaghetti n.5 500 g", "Barilla", 30),
    offer("12", "Yoghurt greco bianco 150 g", "Fage", 20),
]


def item(title, notes=None, iid=None):
    return ShoppingItem(id=iid or title, title=title, notes=notes)


@pytest.fixture
def matcher():
    return Matcher()


def ids(cands):
    return [c.offer.id for c in cands]


def test_normalize_and_stem():
    assert normalize("Caffè dell'Orzo 1,5L") == ["caffe", "dell", "orzo", "1", "5l"]
    assert stem("pomodori") == stem("pomodoro") == "pomodor"
    assert stem("funghi") == stem("fungo") == "fung"
    assert stem("mele") == stem("mela")
    assert content_stems("Pomodori pelati 3x400g") == ["pomodor", "pelat"]


def test_latte_matches_milk_but_not_chocolate_or_cleanser(matcher):
    [r] = matcher.match([item("latte")], OFFERS)
    assert set(ids(r.matches)) == {"1", "2"}
    assert ids(r.doubts) == ["3"]          # cioccolato AL latte → dubbio
    assert "4" not in ids(r.matches + r.doubts)  # latte detergente → escluso


def test_notes_rank_preferred_offer_first(matcher):
    [r] = matcher.match([item("latte", notes="intero")], OFFERS)
    assert ids(r.matches)[0] == "1"
    [r] = matcher.match([item("latte")], OFFERS)
    assert ids(r.matches)[0] == "2"  # a parità, sconto maggiore


def test_plural_and_synonyms(matcher):
    [pomodori, pelati, passata, funghi] = matcher.match(
        [item("pomodori"), item("pelati"), item("passata"), item("fungo")], OFFERS
    )
    assert "5" in ids(pomodori.matches)
    assert ids(pelati.matches) == ["5"]
    assert ids(passata.matches) == ["6"]
    assert ids(funghi.matches) == ["10"]


def test_flavour_is_a_doubt(matcher):
    [r] = matcher.match([item("caffè")], OFFERS)
    assert ids(r.matches) == ["8"]
    assert ids(r.doubts) == ["7"]


def test_typo_and_no_match(matcher):
    [yog, pane] = matcher.match([item("yogurt"), item("pane")], OFFERS)
    assert ids(yog.matches) == ["12"]
    assert pane.matches == [] and pane.doubts == []


def test_partial_coverage_is_a_doubt(matcher):
    [r] = matcher.match([item("latte di soia")], OFFERS)
    assert r.matches == []
    assert {c.status for c in r.doubts} == {DUBBIO}


def test_config_overrides():
    m = Matcher(synonyms={"pasta": ["spaghetti", "penne"]}, exclude={"latte": ["scremato"]})
    [pasta, latte] = m.match([item("pasta"), item("latte")], OFFERS)
    assert ids(pasta.matches) == ["11"]
    assert ids(latte.matches) == ["1"]


def test_max_doubts():
    many = [offer(str(i), f"Biscotti al latte {i}") for i in range(20)]
    [r] = Matcher(max_doubts=5).match([item("latte")], many)
    assert len(r.doubts) == 5


# --- seconda passata con Claude (client finto: nessuna rete) ---

class FakeClaude:
    def __init__(self, decisions, stop_reason="end_turn"):
        self.decisions, self.stop_reason, self.calls = decisions, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = json.dumps({"decisions": self.decisions})
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


def test_llm_confirms_and_rejects_doubts(matcher, tmp_path):
    results = matcher.match([item("latte", iid="L"), item("caffè", iid="C")], OFFERS)
    fake = FakeClaude([{"item_id": "L", "offer_ids": []}, {"item_id": "C", "offer_ids": ["7", "999"]}])
    moved = llm.resolve(results, api_key="k", model="claude-opus-5-5", cache_dir=tmp_path, client=fake)
    latte, caffe = results
    assert moved == 1
    assert latte.doubts == [] and set(ids(latte.matches)) == {"1", "2"}
    assert set(ids(caffe.matches)) == {"7", "8"} and caffe.doubts == []
    assert [c.source for c in caffe.matches if c.offer.id == "7"] == ["llm"]

    req = fake.calls[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    sent = json.loads(req["messages"][0]["content"])
    assert {p["item_id"] for p in sent} == {"L", "C"}  # solo voci con dubbi
    assert "detergente" not in req["messages"][0]["content"]  # gli esclusi non vengono inviati


def test_llm_cache_avoids_second_call(matcher, tmp_path):
    fake = FakeClaude([{"item_id": "C", "offer_ids": []}])
    for _ in range(2):
        results = matcher.match([item("caffè", iid="C")], OFFERS)
        llm.resolve(results, api_key="k", model="m", cache_dir=tmp_path, client=fake)
    assert len(fake.calls) == 1


def test_llm_refusal_keeps_local_results(matcher, tmp_path):
    results = matcher.match([item("caffè", iid="C")], OFFERS)
    fake = FakeClaude([], stop_reason="refusal")
    assert llm.resolve(results, api_key="k", model="m", cache_dir=tmp_path, client=fake) == 0
    assert ids(results[0].doubts) == ["7"]


def test_llm_scope_all_can_drop_fuzzy_matches(matcher):
    results = matcher.match([item("latte", iid="L")], OFFERS)
    fake = FakeClaude([{"item_id": "L", "offer_ids": ["1"]}])
    llm.resolve(results, api_key="k", model="m", scope="all", client=fake)
    assert ids(results[0].matches) == ["1"]


def test_nothing_to_ask(matcher):
    results = matcher.match([item("uova")], OFFERS)
    fake = FakeClaude([])
    assert llm.resolve(results, api_key="k", model="m", client=fake) == 0
    assert fake.calls == [] and results[0].matches[0].status == SICURO

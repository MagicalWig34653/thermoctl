"""Die öffentliche Seite (`site/`): erzeugt, vollständig, und ohne Dritte.

Die Seite ist öffentlich, und niemand liest sie beim Zusammenführen Zeile für Zeile.
Deshalb prüfen diese Tests, was bei einer öffentlichen Seite schiefgehen kann, ohne
dass es auffällt:

* **Synchron** -- `site/` ist exakt das, was `tools/landingpage_erzeugen.py` aus
  `thermoctl/data/glossar.json` und `docs/bilder/` erzeugt. Von Hand geänderte
  Seiten oder ein vergessenes Neuerzeugen schlagen fehl (wie bei `docs/glossar.md`).
* **Vollständig** -- jede Glossar-Kennung ist ein Anker, jeder Verweis führt irgendwohin,
  jedes Bild ist da, hat eine Beschreibung und ist eine unveränderte Kopie.
* **Ohne Dritte** -- jede Adresse nach außen steht in der Positivliste, nichts wird von
  fremden Servern geladen, nichts im Browser gespeichert.
* **Lizenz** -- jede Seite nennt AGPL-3.0.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from thermoctl.domain.glossary import Glossary, GlossaryError, default_glossary, parse_glossary
from tools import landingpage_erzeugen as lp

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
SEITEN = ("index.html", "glossar.html", "404.html")

URL_MUSTER = re.compile(r"https?://[^\s\"'<>)\\]+")
IP_MUSTER = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
MAIL_MUSTER = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class _Sammler(HTMLParser):
    """Sammelt Etiketten, Anker, Verweise und Texte einer Seite."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.ids: list[str] = []
        self.texte: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        daten = dict(attrs)
        self.tags.append((tag, daten))
        if daten.get("id"):
            self.ids.append(str(daten["id"]))

    def handle_data(self, data: str) -> None:
        self.texte.append(data)

    def attribut(self, tag: str, name: str) -> list[str]:
        return [str(a[name]) for t, a in self.tags if t == tag and a.get(name) is not None]

    def etiketten(self, tag: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.tags if t == tag]


def _lies(name: str) -> str:
    return (SITE / name).read_text(encoding="utf-8")


def _parse(name: str) -> _Sammler:
    sammler = _Sammler()
    sammler.feed(_lies(name))
    return sammler


def _alle_textdateien() -> dict[str, str]:
    return {
        name: _lies(name) for name in (*SEITEN, "stil.css", "glossar.js", "favicon.svg")
    }


# --------------------------------------------------------------------------------------
# Synchron mit dem Generator
# --------------------------------------------------------------------------------------


def test_site_directory_matches_what_the_generator_produces() -> None:
    probleme = lp.abweichungen(lp.erzeuge())
    assert not probleme, (
        "site/ ist nicht aktuell (" + "; ".join(probleme) + "). "
        "Neu erzeugen: .venv/bin/python -m tools.landingpage_erzeugen"
    )


def test_the_generator_is_deterministic() -> None:
    assert lp.erzeuge() == lp.erzeuge()


def test_check_mode_reports_a_stale_site_and_exits_with_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(lp, "ZIEL", tmp_path)
    assert lp.main(["--pruefen"]) == 1
    assert "nicht aktuell" in capsys.readouterr().err
    assert lp.main([]) == 0
    assert lp.main(["--pruefen"]) == 0
    assert "aktuell" in capsys.readouterr().out
    (tmp_path / "index.html").write_text("von Hand geändert", encoding="utf-8")
    assert lp.main(["--pruefen"]) == 1


def test_writing_removes_files_that_no_longer_belong_and_empty_directories(
    tmp_path: Path,
) -> None:
    dateien = lp.erzeuge()
    lp.schreibe(dateien, tmp_path)
    (tmp_path / "alt.html").write_text("x", encoding="utf-8")
    (tmp_path / "leer").mkdir()
    (tmp_path / "bilder" / "alt.png").write_bytes(b"x")
    assert sorted(lp.abweichungen(dateien, tmp_path)) == [
        "überzählig: alt.html",
        "überzählig: bilder/alt.png",
    ]
    lp.schreibe(dateien, tmp_path)
    assert lp.abweichungen(dateien, tmp_path) == []
    assert not (tmp_path / "leer").exists()


def test_a_missing_file_is_reported_as_missing(tmp_path: Path) -> None:
    dateien = lp.erzeuge()
    assert "fehlt: index.html" in lp.abweichungen(dateien, tmp_path)


def test_a_broken_glossary_stops_the_generator_with_exit_code_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def kaputt() -> Glossary:
        raise GlossaryError("Eintrag fehlt")

    monkeypatch.setattr(lp, "default_glossary", kaputt)
    assert lp.main(["--pruefen"]) == 2
    assert "Eintrag fehlt" in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# Glossar
# --------------------------------------------------------------------------------------


def test_every_glossary_id_is_an_anchor_on_the_glossary_page() -> None:
    seite = _parse("glossar.html")
    fehlend = sorted(default_glossary().ids() - set(seite.ids))
    assert not fehlend, f"Kein Anker auf glossar.html für: {', '.join(fehlend)}"
    artikel = [a for a in seite.etiketten("article") if a.get("class") == "entry"]
    assert len(artikel) == len(default_glossary().entries)
    assert {str(a["id"]) for a in artikel} == default_glossary().ids()


def test_the_anchors_of_the_glossary_are_exactly_the_json_ids_not_derived_from_the_text() -> None:
    glossar = parse_glossary(
        {
            "version": 1,
            "entries": [
                {
                    "id": "zweiter-eintrag",
                    "term": "Ärger & Öl",
                    "short": "Kurz.",
                    "explanation": "Lang.",
                    "area": "Regelung",
                }
            ],
        }
    )
    seite = lp.render_glossar(glossar)
    assert 'id="zweiter-eintrag"' in seite
    assert "Ärger &amp; Öl" in seite
    # Ä zählt als A: der Eintrag steht unter "A", und die Sprungmarke zeigt dorthin.
    assert 'href="#buchstabe-a"' in seite
    assert 'id="buchstabe-a"' in seite


def test_glossary_text_is_escaped_and_doc_paths_become_links() -> None:
    glossar = parse_glossary(
        {
            "version": 1,
            "entries": [
                {
                    "id": "probe",
                    "term": "Probe <b>fett</b>",
                    "short": 'Kurz mit "Anführungszeichen" & <script>alert(1)</script>.',
                    "explanation": "Siehe docs/api.md). Und <i>so</i>.",
                    "area": "Regelung",
                    "synonyms": ["<x>"],
                }
            ],
        }
    )
    seite = lp.render_glossar(glossar)
    assert "<b>fett</b>" not in seite
    assert "<script>alert" not in seite
    assert "&lt;script&gt;" in seite
    assert "<i>so</i>" not in seite
    assert f'<a href="{lp.REPO}/blob/main/docs/api.md"><code>docs/api.md</code></a>' in seite


def test_see_also_links_and_letter_marks_lead_to_existing_anchors() -> None:
    seite = _parse("glossar.html")
    ids = set(seite.ids)
    ziele = [h[1:] for h in seite.attribut("a", "href") if h.startswith("#")]
    assert ziele
    assert [z for z in ziele if z not in ids] == []
    # Jede Sprungmarke trägt einen Buchstaben, der auch eine Gruppe hat.
    marken = {a["data-buchstabe"] for a in seite.etiketten("a") if a.get("data-buchstabe")}
    gruppen = {a["data-gruppe"] for a in seite.etiketten("section") if a.get("data-gruppe")}
    assert marken == gruppen == set(default_glossary().letters())


def test_the_glossary_search_is_hidden_until_the_script_reveals_it() -> None:
    seite = _lies("glossar.html")
    assert 'id="glossar-suchbox"' in seite and "hidden>" in seite
    skript = _lies("glossar.js")
    assert "box.hidden = false" in skript
    assert '<script src="glossar.js"></script>' in seite
    assert "<script" not in _lies("index.html")


def test_every_index_card_links_to_a_glossary_entry_that_exists() -> None:
    ziele = [
        h.split("#", 1)[1]
        for h in _parse("index.html").attribut("a", "href")
        if h.startswith("glossar.html#")
    ]
    assert ziele
    fehlend = sorted(set(ziele) - default_glossary().ids())
    assert not fehlend, f"index.html verweist auf unbekannte Glossar-Kennungen: {fehlend}"
    # Und die Konstanten, aus denen die Karten entstehen, sind dieselben Kennungen.
    benutzt = {kennung for funktion in lp.FUNKTIONEN for kennung in funktion.begriffe}
    assert benutzt <= default_glossary().ids()


# --------------------------------------------------------------------------------------
# Verweise: nach außen nur die Positivliste, nach innen nur Erreichbares
# --------------------------------------------------------------------------------------


def test_every_address_on_the_site_is_on_the_allowlist() -> None:
    fremd: list[str] = []
    for name, text in _alle_textdateien().items():
        if name == "favicon.svg":
            continue  # der SVG-Namensraum ist eine Kennung, keine Anfrage
        for treffer in URL_MUSTER.findall(text):
            if not treffer.startswith(lp.ERLAUBTE_VERWEISE):
                fremd.append(f"{name}: {treffer}")
    assert not fremd, "Adressen außerhalb der Positivliste:\n  " + "\n  ".join(fremd)


def test_the_allowlist_itself_is_narrow() -> None:
    # Eine Positivliste, in die jemand "https://" einträgt, prüft nichts mehr.
    for eintrag in lp.ERLAUBTE_VERWEISE:
        assert "/" in eintrag.removeprefix("https://") and len(eintrag) > 12, eintrag
    assert not any(eintrag in ("http://", "https://") for eintrag in lp.ERLAUBTE_VERWEISE)


def test_nothing_is_loaded_from_third_parties() -> None:
    for name in SEITEN:
        seite = _parse(name)
        # Skripte nur lokal und nur die eine Datei.
        for quelle in seite.attribut("script", "src"):
            assert quelle == "glossar.js", f"{name}: Skript {quelle}"
        # Stylesheets, Symbole und Bilder: relativ oder vom eigenen Pfad, nie mit Schema.
        for etikett in seite.etiketten("link"):
            ziel = str(etikett.get("href"))
            if etikett.get("rel") == "canonical":
                assert ziel.startswith(lp.SEITEN_URL)
                continue
            assert not re.match(r"^(?:[a-z]+:)?//", ziel), f"{name}: link {ziel}"
        for ziel in seite.attribut("img", "src"):
            assert not re.match(r"^(?:[a-z]+:)?//", ziel), f"{name}: img {ziel}"
        # Keine eingebetteten Fremdinhalte, keine Formulare (nichts wird abgeschickt).
        for etikett in ("iframe", "object", "embed", "form", "video", "audio", "base"):
            assert not seite.etiketten(etikett), f"{name}: <{etikett}>"
    css = _lies("stil.css")
    assert "@import" not in css and "@font-face" not in css
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", css)


def test_the_site_stores_nothing_and_sends_nothing() -> None:
    for name in ("glossar.js", *SEITEN):
        text = _lies(name)
        for verboten in (
            "document.cookie",
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "XMLHttpRequest",
            "fetch(",
            "sendBeacon",
            "WebSocket",
            "navigator.serviceWorker",
        ):
            assert verboten not in text, f"{name}: {verboten}"


def test_local_references_resolve_to_files_and_anchors() -> None:
    ids = {name: set(_parse(name).ids) for name in SEITEN}
    kaputt: list[str] = []
    for name in SEITEN:
        seite = _parse(name)
        verweise = [
            *seite.attribut("a", "href"),
            *seite.attribut("link", "href"),
            *seite.attribut("script", "src"),
            *seite.attribut("img", "src"),
        ]
        for verweis in verweise:
            if verweis.startswith(("http://", "https://", "mailto:")):
                continue
            pfad, _, anker = verweis.partition("#")
            pfad = pfad.removeprefix(lp.BASIS_PFAD)
            ziel = pfad or name
            if ziel in ("", "./"):
                ziel = "index.html"
            if not (SITE / ziel).is_file():
                kaputt.append(f"{name}: {verweis} (Datei fehlt)")
            elif anker and anker not in ids.get(ziel, set()):
                kaputt.append(f"{name}: {verweis} (Anker fehlt)")
    assert not kaputt, "\n".join(kaputt)


class _Bilanz(HTMLParser):
    """Prüft, dass jedes geöffnete Etikett wieder geschlossen wird."""

    LEER = {"meta", "link", "img", "input", "br", "hr"}

    def __init__(self) -> None:
        super().__init__()
        self.stapel: list[str] = []
        self.fehler: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self.LEER:
            self.stapel.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.LEER:
            return
        if not self.stapel or self.stapel[-1] != tag:
            self.fehler.append(f"</{tag}> passt nicht zu {self.stapel[-1:]}")
        else:
            self.stapel.pop()


@pytest.mark.parametrize("name", SEITEN)
def test_markup_is_balanced_and_ids_are_unique(name: str) -> None:
    bilanz = _Bilanz()
    bilanz.feed(_lies(name))
    assert not bilanz.fehler and not bilanz.stapel, (bilanz.fehler[:3], bilanz.stapel)
    kennungen = _parse(name).ids
    doppelt = sorted({k for k in kennungen if kennungen.count(k) > 1})
    assert not doppelt, f"{name}: doppelte id {doppelt}"


def test_skip_link_and_main_landmark_exist_on_every_page() -> None:
    for name in SEITEN:
        seite = _parse(name)
        assert "inhalt" in seite.ids, name
        assert "#inhalt" in seite.attribut("a", "href"), name
        assert [a.get("id") for a in seite.etiketten("main")] == ["inhalt"], name


def test_links_into_the_repository_point_at_files_that_exist() -> None:
    praefix = f"{lp.REPO}/blob/main/"
    geprueft = 0
    for name in SEITEN:
        for verweis in _parse(name).attribut("a", "href"):
            if verweis.startswith(praefix):
                pfad = verweis.removeprefix(praefix).split("#", 1)[0]
                assert (ROOT / pfad).is_file(), f"{name}: {verweis} zeigt auf nichts"
                geprueft += 1
            elif verweis.startswith(f"{lp.REPO}/tree/main/"):
                pfad = verweis.removeprefix(f"{lp.REPO}/tree/main/")
                assert (ROOT / pfad).is_dir(), f"{name}: {verweis} zeigt auf nichts"
                geprueft += 1
    assert geprueft >= 10


def test_the_docker_image_name_matches_the_one_the_workflow_pushes() -> None:
    compose = (ROOT / "docker" / "compose.beispiel.yml").read_text(encoding="utf-8")
    assert f"image: {lp.ABBILD}:latest" in compose
    # docker.yml baut `ghcr.io/${{ github.repository }}`; GitHub macht daraus Kleinbuchstaben.
    docker = (ROOT / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")
    assert "images: ghcr.io/${{ github.repository }}" in docker
    assert lp.ABBILD == "ghcr.io/" + lp.REPO.removeprefix("https://github.com/").lower()
    assert lp.ABBILD in _lies("index.html")


def test_the_pages_url_is_consistent_with_the_base_path_and_the_readme() -> None:
    assert lp.SEITEN_URL.endswith(lp.BASIS_PFAD)
    assert lp.SEITEN_URL.startswith("https://")
    assert lp.SEITEN_URL in (ROOT / "README.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Inhalt, Lizenz, nichts Privates
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", SEITEN)
def test_every_page_names_the_license_and_links_the_source(name: str) -> None:
    text = _lies(name)
    assert "AGPL-3.0" in text
    assert f"{lp.REPO}/blob/main/LICENSE" in text
    assert f'href="{lp.REPO}"' in text


@pytest.mark.parametrize("name", SEITEN)
def test_every_page_has_the_basics(name: str) -> None:
    seite = _parse(name)
    text = _lies(name)
    assert text.startswith("<!doctype html>\n<html lang=\"de\">")
    assert '<meta charset="utf-8">' in text
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in text
    assert len(seite.etiketten("h1")) == 1
    beschreibung = [
        a["content"] for a in seite.etiketten("meta") if a.get("name") == "description"
    ]
    assert len(beschreibung) == 1 and 40 <= len(str(beschreibung[0])) <= 300
    titel = re.search(r"<title>([^<]+)</title>", text)
    assert titel is not None and "thermoctl" in titel.group(1)
    # Open Graph: nur die eigene Adresse, nie eine fremde.
    for a in seite.etiketten("meta"):
        if str(a.get("property", "")).startswith("og:") and "http" in str(a.get("content")):
            assert str(a["content"]).startswith(lp.SEITEN_URL)
    if name == "404.html":
        assert any(a.get("name") == "robots" for a in seite.etiketten("meta"))
    else:
        assert any(a.get("rel") == "canonical" for a in seite.etiketten("link"))


def test_the_index_page_is_honest_about_what_the_software_does_not_promise() -> None:
    text = _lies("index.html")
    assert "Trockenlauf" in text and "keine Zusagen" in text
    assert "Beta" in text
    # Keine Zusage über Einsparungen oder Wirkung auf der Startseite.
    for versprechen in ("spart", "Einsparung", "günstiger", "garantiert", "gesenkt"):
        assert versprechen not in text, versprechen


def test_no_addresses_mail_adresses_or_secrets_appear_on_the_site() -> None:
    for name, text in _alle_textdateien().items():
        assert IP_MUSTER.findall(text) in ([], ["127.0.0.1"]), name
        assert not MAIL_MUSTER.findall(text), name
        for verdaechtig in ("Bearer ", "BEGIN PRIVATE", "THERMOCTL_SECRET_KEY=", "token="):
            assert verdaechtig not in text, f"{name}: {verdaechtig}"
    # Der Schlüssel erscheint nur als Name, nie mit einem Wert.
    assert "THERMOCTL_SECRET_KEY" in _lies("index.html")


# --------------------------------------------------------------------------------------
# Bilder
# --------------------------------------------------------------------------------------


def test_every_image_exists_has_a_description_and_is_an_unchanged_copy() -> None:
    seite = _parse("index.html")
    bilder = [a for a in seite.etiketten("img") if str(a["src"]).startswith("bilder/")]
    assert len(bilder) == 1 + len(lp.EINBLICKE)
    for bild in bilder:
        datei = str(bild["src"]).removeprefix("bilder/")
        quelle = ROOT / "docs" / "bilder" / datei
        assert quelle.is_file(), f"{datei} liegt nicht in docs/bilder/"
        assert (SITE / "bilder" / datei).read_bytes() == quelle.read_bytes()
        assert len(str(bild.get("alt", "")).strip()) >= 20, f"{datei}: Beschreibung fehlt"
        breite, hoehe = lp.png_groesse(quelle.read_bytes())
        assert (str(breite), str(hoehe)) == (bild["width"], bild["height"]), datei
    vorhanden = {p.name for p in (SITE / "bilder").iterdir()}
    assert vorhanden == {str(b["src"]).removeprefix("bilder/") for b in bilder}


def test_all_images_come_from_the_demo_data_screenshots() -> None:
    # Nur Bilder, die die Dokumentation selbst einbindet, also aus tools/screenshots.py
    # (--doku) mit den erfundenen Demodaten entstehen.
    eingebunden = set(re.findall(r"bilder/([\w-]+\.png)", (ROOT / "docs/bedienung.md").read_text()))
    eingebunden |= set(re.findall(r"docs/bilder/([\w-]+\.png)", (ROOT / "README.md").read_text()))
    eingebunden |= set(re.findall(r"bilder/([\w-]+\.png)", (ROOT / "docs/wohnung.md").read_text()))
    for bild in (lp.HERO_BILD, *lp.EINBLICKE):
        assert bild.datei in eingebunden, f"{bild.datei} ist kein dokumentiertes Demobild"
        assert "Demodaten" in bild.alt
    assert 'erfundenen Demodaten' in _lies("index.html")


def test_png_size_is_read_from_the_header_and_a_non_png_is_refused() -> None:
    datei = (ROOT / "docs" / "bilder" / "wohnung-startseite-mobil.png").read_bytes()
    assert lp.png_groesse(datei) == (390, 844)
    with pytest.raises(ValueError, match="PNG"):
        lp.png_groesse(b"GIF89a" + b"\0" * 40)


def test_portrait_screenshots_are_shown_whole_and_wide_ones_cropped() -> None:
    bild = lp.EINBLICKE[0]
    assert 'class="shot portrait"' in lp._bild(bild, (390, 844), zuschnitt=True)
    assert 'class="shot crop"' in lp._bild(bild, (1280, 900), zuschnitt=True)
    assert 'class="shot"' in lp._bild(bild, (1280, 900), zuschnitt=False)
    assert 'loading="eager"' in lp._bild(bild, (1280, 900), zuschnitt=False)
    assert 'loading="lazy"' in lp._bild(bild, (1280, 900), zuschnitt=True)


# --------------------------------------------------------------------------------------
# Gestaltung
# --------------------------------------------------------------------------------------


def test_the_stylesheet_follows_the_device_setting_and_reduced_motion() -> None:
    css = _lies("stil.css")
    assert "prefers-color-scheme: dark" in css
    assert "prefers-reduced-motion: no-preference" in css  # sanftes Scrollen nur dann
    assert "scroll-behavior: smooth" in css
    assert ":focus-visible" in css
    assert "color-scheme: light dark" in css
    # Systemschrift, keine Webschrift.
    assert "ui-sans-serif" in css and "system-ui" in css
    assert "fonts.googleapis" not in css


def test_the_stylesheet_and_script_are_unchanged_copies_of_their_sources() -> None:
    quelle = ROOT / "tools" / "landingpage"
    assert (SITE / "stil.css").read_bytes() == (quelle / "stil.css").read_bytes()
    assert (SITE / "glossar.js").read_bytes() == (quelle / "glossar.js").read_bytes()
    assert (SITE / "favicon.svg").read_bytes() == (
        ROOT / "thermoctl" / "web" / "static" / "favicon.svg"
    ).read_bytes()


# --------------------------------------------------------------------------------------
# Der Workflow
# --------------------------------------------------------------------------------------


def _workflow() -> dict[str, object]:
    geladen = yaml.safe_load((ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8"))
    assert isinstance(geladen, dict)
    return geladen


def test_the_pages_workflow_runs_only_for_main_and_by_hand_never_for_pull_requests() -> None:
    wf = _workflow()
    ausloeser = wf[True] if True in wf else wf["on"]  # YAML liest `on` als Wahrheitswert
    assert isinstance(ausloeser, dict)
    assert set(ausloeser) == {"push", "workflow_dispatch"}
    assert ausloeser["push"]["branches"] == ["main"]
    assert {
        "site/**",
        "thermoctl/data/glossar.json",
        "tools/landingpage_erzeugen.py",
    } <= set(ausloeser["push"]["paths"])
    jobs = wf["jobs"]
    assert isinstance(jobs, dict)
    assert "pull_request" not in str(ausloeser)
    assert "pull_request" in jobs["veroeffentlichen"]["if"]  # ausdrücklich ausgeschlossen
    assert "refs/heads/main" in jobs["veroeffentlichen"]["if"]


def test_the_pages_workflow_has_minimal_permissions_only_where_deploying() -> None:
    wf = _workflow()
    assert wf["permissions"] == {"contents": "read"}
    jobs = wf["jobs"]
    assert isinstance(jobs, dict)
    assert "permissions" not in jobs["pruefen"]
    assert jobs["veroeffentlichen"]["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert jobs["veroeffentlichen"]["needs"] == "pruefen"


def test_the_pages_workflow_pins_every_action_to_a_commit_and_checks_before_deploying() -> None:
    text = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
    verwendet = re.findall(r"uses:\s*(\S+)", text)
    assert len(verwendet) >= 5
    for eintrag in verwendet:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", eintrag), eintrag
    assert "python -m tools.landingpage_erzeugen --pruefen" in text
    assert "tests/test_landingpage.py" in text
    assert "path: site" in text

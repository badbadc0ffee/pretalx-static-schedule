import posixpath
from urllib.parse import quote

import pytest
from bs4 import BeautifulSoup
from django.utils.translation import get_language, override
from django_scopes import scope

from pretalx_static_schedule import __version__
from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("images", (False, True))
@pytest.mark.parametrize("locale", ("de", "fr"))
@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net:8443"))
def test_every_page_has_the_shared_semantic_shell(
    image_uploads, event, images, locale, custom_domain
):
    uploads, _ = image_uploads
    with scope(event=event):
        event.name = {"en": "Event & <Example>", "de": "Veranstaltung"}
        event.custom_domain = custom_domain
        if not images:
            event.logo = ""
            event.header_image = ""
        event.save()
        event.refresh_from_db()
        with override(locale):
            files = StaticScheduleExporter(event).get_files()
            assert get_language() == locale

    kinds = set()
    for name, content in files.items():
        if not name.endswith(".html"):
            continue
        page = BeautifulSoup(content, "html.parser")
        prefix = "../" if posixpath.dirname(name) else ""
        assert [child.name for child in page.body.find_all(recursive=False)] == [
            "header",
            "main",
            "footer",
        ]
        assert page.html["lang"] == "en"
        assert page.head.meta["charset"] == "utf-8"
        assert page.head.find("meta", attrs={"name": "viewport"})
        assert not page.find(["style", "script"])
        assert len(page.main.find_all("h1")) == 1
        assert not page.header.find(["h1", "h2", "h3"])
        assert page.title.get_text().endswith(" – Event & <Example>")
        assert not page.find("example")
        kinds.add(page.main["data-page"])
        if page.main["data-page"] in {"day", "track", "room"}:
            assert f"data-{page.main['data-page']}" in page.main.attrs
        assert [link["href"] for link in page.head.select('[rel="stylesheet"]')] == [
            prefix + "assets/variables.css",
            prefix + "assets/schedule.css",
        ]

        event_link = page.header.select_one('[data-field="event"]')
        assert event_link["href"] == prefix + "index.html"
        assert not event_link.get_text()
        assert event_link.img["alt"] == "40C3"
        assert event_link.img["src"] == prefix + "assets/logo.svg"
        for field, label, value in (
            ("schedule-version", "Schedule version", "1.0"),
            ("timezone", "Timezone", event.timezone),
        ):
            facts = page.header.select(f'[data-field="{field}"]')
            assert len(facts) == 1
            assert facts[0].dt["aria-label"] == label
            assert not facts[0].dt.get_text()
            assert facts[0].dd.get_text() == value

        assert len(page.header.find_all("img")) == (2 if images else 1)
        if images:
            banner = page.header.select_one('[data-field="banner"]')
            assert banner["alt"] == "Event & <Example>"
            assert banner["src"] == prefix + quote(f"media/{uploads['header'].name}")

        assert quote(f"media/{uploads['logo'].name}") not in files

        source = page.footer.a
        assert source["href"] == event.urls.schedule.full()
        assert source.get_text() == "Source schedule"
        assert "external" in source["rel"]
        version = page.footer.select_one('data[data-field="exporter-version"]')
        assert version["value"] == __version__
        assert " ".join(version.get_text().split()) == (
            f"pretalx-static-schedule {__version__}"
        )
        for element in [page.head, page.header, page.footer]:
            for child in element.find_all():
                assert (
                    not {"style", "class", "align", "bgcolor", "width", "height"}
                    & child.attrs.keys()
                )

    assert kinds == {
        "sessions",
        "day",
        "track",
        "room",
        "session",
        "speaker",
        "speakers",
    }


def test_shared_captions_and_current_navigation_style_are_external(
    published_talk_slot, event
):
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()
    css = files["assets/schedule.css"].decode()
    for attribute, value, caption in (
        ("data-group", "sessions", "Sessions"),
        ("data-group", "days", "Days"),
        ("data-group", "speakers", "Speakers"),
        ("data-group", "tracks", "Tracks"),
        ("data-group", "rooms", "Rooms"),
        ("data-field", "schedule-version", "Schedule version"),
        ("data-field", "timezone", "Timezone"),
        ("data-label", "all-sessions", "All sessions"),
        ("data-label", "all-speakers", "All speakers"),
    ):
        assert f'[{attribute}="{value}"]' in css
        assert f'content: "{caption}";' in css
    assert 'header nav [aria-current="page"]' in css
    for name, label in (
        ("index.html", "All sessions"),
        ("speakers/index.html", "All speakers"),
    ):
        page = BeautifulSoup(files[name], "html.parser")
        assert page.main.h1["aria-label"] == label
        assert not page.main.h1.get_text()

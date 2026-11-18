from copy import deepcopy
from io import BytesIO
from urllib.parse import unquote, urljoin, urlsplit
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django_scopes import scope

from pretalx.person.models import SpeakerProfile, User

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def test_speaker_overview_sorts_full_display_names_ignoring_case(
    published_talk_slot, event
):
    with scope(event=event):
        submission = published_talk_slot.submission
        submission.speakers.clear()
        speakers = [
            SpeakerProfile.objects.create(event=event, code=code, name=name)
            for code, name in (
                ("SORT003", "bob Example"),
                ("SORT002", "Ada Example"),
                ("SORT006", "ada example"),
                ("SORT001", "Ada Example"),
                ("SORT004", "alice & <team>"),
                ("SORT005", "Éva Example"),
            )
        ]
        speakers.append(
            SpeakerProfile.objects.create(
                event=event,
                code="SORT007",
                name="",
                user=User.objects.create_user(
                    email="carol@example.org", name="Carol Example"
                ),
            )
        )
        submission.speakers.add(*speakers)
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["speakers/index.html"], "html.parser")
    assert [
        (link["href"], link.get_text())
        for link in page.select(
            'main > [data-rel="speakers"] > li > article[data-kind="speaker"] > h2 > a'
        )
    ] == [
        ("SORT001.html", "Ada Example"),
        ("SORT002.html", "Ada Example"),
        ("SORT006.html", "ada example"),
        ("SORT004.html", "alice & <team>"),
        ("SORT003.html", "bob Example"),
        ("SORT007.html", "Carol Example"),
        ("SORT005.html", "Éva Example"),
    ]
    assert not page.find("team")
    for link in page.select(
        'main > [data-rel="speakers"] > li > article[data-kind="speaker"] > h2 > a'
    ):
        detail = BeautifulSoup(files[f"speakers/{link['href']}"], "html.parser")
        assert detail.h1.get_text() == link.get_text()


@pytest.mark.parametrize("deployment", ("/", "/conference/2026/"))
def test_speaker_overview_links_and_assets_work_after_extraction(
    image_uploads, event, tmp_path, deployment
):
    with scope(event=event):
        _, _, data = StaticScheduleExporter(event).render(request=None)
    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(tmp_path)

    page = BeautifulSoup((tmp_path / "speakers/index.html").read_text(), "html.parser")
    assert page.main.h1["aria-label"] == "All speakers"
    assert page.title.get_text() == f"All speakers – {event.name}"
    assert page.header.find("a")["href"] == "../index.html"
    assert page.select_one('[data-field="schedule-version"] dd').get_text() == "1.0"
    assert (
        page.select_one(
            'main > [data-rel="speakers"] > li > article[data-kind="speaker"] > h2 > a'
        )["href"]
        == "ADA0001.html"
    )
    talk = page.select_one('[data-rel="sessions"] > li > a')
    assert talk["href"] == "../sessions/TOOLS01.html"
    assert talk.get_text() == "Building <tools>"
    assert [link["href"] for link in page.find_all("link", rel="stylesheet")] == [
        "../assets/variables.css",
        "../assets/schedule.css",
    ]
    for element in page.select("[href], [src]"):
        reference = element.get("href", element.get("src"))
        assert not reference.startswith("/")
        if urlsplit(reference).scheme:
            assert reference in {event.urls.schedule.full(), "https://example.org"}
            continue
        resolved = urlsplit(
            urljoin(
                f"https://static.example.org{deployment}speakers/index.html", reference
            )
        )
        assert resolved.path.startswith(deployment)
        assert (tmp_path / unquote(resolved.path.removeprefix(deployment))).is_file()


@pytest.mark.parametrize("absolute", (False, True))
@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net:8443"))
def test_online_speaker_overview_links_are_rewritten(
    published_talk_slot, event, settings, tmp_path, absolute, custom_domain
):
    templates = deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"].insert(0, tmp_path)
    settings.TEMPLATES = templates
    override = tmp_path / "pretalx_static_schedule/speaker_overview.html"
    override.parent.mkdir()
    url_variable = "event.urls.speakers.full" if absolute else "event.urls.speakers"
    override.write_text(
        '{% extends "pretalx_static_schedule/speaker_overview.html" %}'
        '{% block content %}{{ block.super }}<a class="original-overview" href="{{ '
        + url_variable
        + ' }}">Original overview</a>{% endblock %}'
    )
    with scope(event=event):
        event.custom_domain = custom_domain
        event.save(update_fields=["custom_domain"])
        url = event.urls.speakers.full() if absolute else event.urls.speakers
        submission = published_talk_slot.submission
        submission.description = f"[All speakers]({url})"
        submission.save(update_fields=["description"])
        speaker = submission.sorted_speakers.first()
        speaker.biography = f"[All speakers]({url})"
        speaker.save(update_fields=["biography"])
        files = StaticScheduleExporter(event).get_files()

    for name, selector, destination in (
        (
            "sessions/TOOLS01.html",
            '[data-field="description"] a',
            "../speakers/index.html",
        ),
        ("speakers/ADA0001.html", '[data-field="biography"] a', "index.html"),
        ("speakers/index.html", ".original-overview", "index.html"),
    ):
        page = BeautifulSoup(files[name], "html.parser")
        assert page.select_one(selector)["href"] == destination

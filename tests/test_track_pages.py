import datetime as dt
from copy import deepcopy
from io import BytesIO
from urllib.parse import unquote, urljoin, urlsplit
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django_scopes import scope

from pretalx.event.models import Event
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.enums import SlotType
from pretalx.schedule.models import Room, TalkSlot
from pretalx.submission.models import Submission, SubmissionStates, Track

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("timezone", ("Europe/Berlin", "Pacific/Auckland"))
def test_track_pages_group_only_their_published_sessions_by_schedule_day(
    published_talk_slot, event, day_files, timezone
):
    with scope(event=event):
        event.timezone = timezone
        event.save(update_fields=["timezone"])
        event = Event.objects.get(pk=event.pk)
        track = published_talk_slot.submission.track
        track.position = 2
        track.save(update_fields=["position"])
        other = Track.objects.create(event=event, name="Other track", position=1)
        empty = Track.objects.create(event=event, name="Empty track", position=0)
        room = Room.objects.create(event=event, name="Room B")
        for code, title, assigned, offset, hour, visible in (
            ("EARLY01", "Earlier session", track, 0, 9, True),
            ("NIGHT01", "Late session", track, 1, 2, True),
            ("NEXTDAY", "Second day session", track, 1, 10, True),
            ("OTHER01", "Other track session", other, 0, 11, True),
            ("NONE001", "Untracked session", None, 0, 12, True),
            ("HIDDEN1", "Unscheduled session", empty, 0, 13, False),
        ):
            submission = Submission.objects.create(
                event=event,
                code=code,
                title=title,
                track=assigned,
                submission_type=event.submission_types.first(),
                state=SubmissionStates.CONFIRMED,
            )
            start = dt.datetime.combine(
                event.date_from + dt.timedelta(days=offset),
                dt.time(hour),
                tzinfo=event.tz,
            )
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=submission,
                room=room if visible else None,
                start=start,
                end=start + dt.timedelta(minutes=30),
            )
        start = dt.datetime.combine(event.date_from, dt.time(15), tzinfo=event.tz)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            room=room,
            slot_type=SlotType.BREAK,
            description="Tea break",
            start=start,
            end=start + dt.timedelta(minutes=15),
        )
        # A second occurrence of the same submission must appear separately.
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=published_talk_slot.submission,
            room=room,
            start=start + dt.timedelta(days=1),
            end=start + dt.timedelta(days=1, minutes=45),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    assert {name for name in files if name.startswith("tracks/")} == {
        f"tracks/{item.pk}.html" for item in (track, other, empty)
    }
    page = BeautifulSoup(files[f"tracks/{track.pk}.html"], "html.parser")
    assert page.h1.get_text() == "Tools"
    assert page.title.get_text(strip=True) == f"Tools – {event.name}"
    assert page.select_one('[data-field="schedule-version"] dd').get_text() == "2.0"
    groups = page.select("section[data-day]")
    assert len(groups) == 2
    assert [group.select_one("h2 a")["href"] for group in groups] == [
        f"../{name}" for name in day_files[:2]
    ]
    assert [
        title.get_text(strip=True)
        for title in groups[0].select(
            'article:is([data-kind="session"], [data-kind="break"]) h3'
        )
    ] == ["Earlier session", "Building <tools>", "Late session"]
    assert [
        title.get_text(strip=True)
        for title in groups[1].select(
            'article:is([data-kind="session"], [data-kind="break"]) h3'
        )
    ] == ["Second day session", "Building <tools>"]
    assert "02:00" in groups[0].get_text()
    assert "Room A" in page.get_text()
    assert "Room B" in page.get_text()
    for excluded in ("Other track session", "Untracked session", "Tea break"):
        assert excluded not in page.get_text()
    other_page = BeautifulSoup(files[f"tracks/{other.pk}.html"], "html.parser")
    assert (
        len(other_page.select('article:is([data-kind="session"], [data-kind="break"])'))
        == 1
    )
    empty_page = BeautifulSoup(files[f"tracks/{empty.pk}.html"], "html.parser")
    assert empty_page.select_one('[data-state="no-sessions"]')
    assert "Unscheduled session" not in empty_page.get_text()
    assert not empty_page.select(
        'section[data-day], article:is([data-kind="session"], [data-kind="break"])'
    )
    overview = BeautifulSoup(files["index.html"], "html.parser")
    assert [link.get_text() for link in overview.select('[data-group="tracks"] a')] == [
        "Empty track",
        "Other track",
        "Tools",
    ]


def test_track_pages_use_the_current_published_schedule(published_talk_slot, event):
    with scope(event=event):
        event.wip_schedule.talks.update(room=None)
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        event.wip_schedule.talks.update(room=published_talk_slot.room)
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(
        files[f"tracks/{published_talk_slot.submission.track_id}.html"], "html.parser"
    )
    assert page.select_one('[data-field="schedule-version"] dd').get_text() == "2.0"
    assert page.select_one('[data-state="no-sessions"]')
    assert not page.select('article:is([data-kind="session"], [data-kind="break"])')


@pytest.mark.parametrize("deployment", ("/", "/conference/2026/"))
def test_track_links_and_assets_work_after_extraction(
    image_uploads, event, day_files, tmp_path, deployment
):
    with scope(event=event):
        submission = event.submissions.get(code="TOOLS01")
        track = submission.track
        _, _, data = StaticScheduleExporter(event).render(request=None)
    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(tmp_path)

    track_file = f"tracks/{track.pk}.html"
    for name, prefix in (
        ("index.html", ""),
        (day_files[0], "../"),
        ("sessions/TOOLS01.html", "../"),
        ("speakers/ADA0001.html", "../"),
        (track_file, "../"),
    ):
        page = BeautifulSoup((tmp_path / name).read_text(), "html.parser")
        assert page.select_one('[data-field="track"] a')["href"] == prefix + track_file
        assert page.find("link", href=prefix + "assets/schedule.css")
        assert page.find("link", href=prefix + "assets/variables.css")
        for element in page.select("[href], [src]"):
            reference = element.get("href", element.get("src"))
            if urlsplit(reference).scheme:
                assert reference in {"https://example.org", event.urls.schedule.full()}
                continue
            assert not reference.startswith("/")
            resolved = urlsplit(
                urljoin(f"https://static.example.org{deployment}{name}", reference)
            )
            assert resolved.path.startswith(deployment)
            assert (
                tmp_path / unquote(resolved.path.removeprefix(deployment))
            ).is_file()
    page = BeautifulSoup((tmp_path / track_file).read_text(), "html.parser")
    assert page.header.find("a")["href"] == "../index.html"
    assert (
        page.select_one('article:is([data-kind="session"], [data-kind="break"]) h3 a')[
            "href"
        ]
        == "../sessions/TOOLS01.html"
    )
    assert (
        page.select_one(
            'article[data-kind="session"] > [data-rel="speakers"] > li > a'
        )["href"]
        == "../speakers/ADA0001.html"
    )
    assert not page.find(["script", "form", "iframe"])


@pytest.mark.parametrize("absolute", (False, True))
@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net"))
def test_track_description_links_and_escaped_names(
    published_talk_slot, event, absolute, custom_domain
):
    with scope(event=event):
        event.custom_domain = custom_domain
        event.save(update_fields=["custom_domain"])
        submission = published_talk_slot.submission
        speaker = submission.sorted_speakers.first()
        track = submission.track
        track.name = "Tools & <practice> / ../"
        track.color = "#2a6b8c"
        track.description = "**Practical tools**\n\n" + " · ".join(
            f"[{label}]({url.full() if absolute else url})"
            for label, url in (
                ("Schedule", event.urls.schedule),
                ("Session", submission.urls.public),
                ("Speaker", speaker.urls.public),
            )
        )
        track.save(update_fields=["name", "description", "color"])
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files[f"tracks/{track.pk}.html"], "html.parser")
    assert page.h1.get_text() == track.name
    assert page.title.get_text(strip=True) == f"{track.name} – {event.name}"
    assert (
        page.select_one('[data-field="description"] strong').get_text()
        == "Practical tools"
    )
    assert [
        (link.get_text(), link["href"])
        for link in page.select('[data-field="description"] a')
    ] == [
        ("Schedule", "../index.html"),
        ("Session", "../sessions/TOOLS01.html"),
        ("Speaker", "../speakers/ADA0001.html"),
    ]
    assert page.main["data-track"] == str(track.pk)
    assert "style" not in page.main.attrs
    assert (
        f'[data-track="{track.pk}"] {{\n    --track-color: #2a6b8c;'
        in files["assets/variables.css"].decode()
    )
    assert not page.find("practice")


def test_track_template_can_extend_the_default(
    published_talk_slot, event, settings, tmp_path
):
    templates = deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"].insert(0, tmp_path)
    settings.TEMPLATES = templates
    override = tmp_path / "pretalx_static_schedule/track.html"
    override.parent.mkdir()
    override.write_text(
        '{% extends "pretalx_static_schedule/track.html" %}'
        "{% block content %}{{ block.super }}<p>Custom track note</p>{% endblock %}"
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(
        files[f"tracks/{published_talk_slot.submission.track_id}.html"], "html.parser"
    )
    assert "Custom track note" in page.get_text()
    assert page.h1.get_text() == "Tools"
    assert (
        len(page.select('article:is([data-kind="session"], [data-kind="break"])')) == 1
    )

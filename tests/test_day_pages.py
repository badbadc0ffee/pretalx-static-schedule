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
from pretalx.submission.models import Submission, SubmissionStates

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "timezone", ("Europe/Berlin", "Pacific/Auckland", "America/Los_Angeles")
)
def test_daily_overviews_follow_pretalx_day_grouping(event, day_files, timezone):
    with scope(event=event):
        event.timezone = timezone
        event.save(update_fields=["timezone"])
        event = Event.objects.get(pk=event.pk)
        rooms = [
            Room.objects.create(event=event, name=name) for name in ("Room A", "Room B")
        ]
        for code, title, offset, hour, room in (
            ("DAYONE1", "First day session", 0, 10, rooms[0]),
            ("EARLY01", "Earlier session", 0, 9, rooms[1]),
            ("DAYTWO1", "Second day session", 1, 10, rooms[0]),
            ("NIGHT01", "Late session", 1, 2, rooms[1]),
        ):
            submission = Submission.objects.create(
                event=event,
                code=code,
                title=title,
                abstract="A session abstract.",
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
                room=room,
                start=start,
                end=start + dt.timedelta(minutes=30),
            )
        for slot_type, description in (
            (SlotType.BREAK, "Tea break"),
            (SlotType.BLOCKER, "Private blocker"),
        ):
            start = dt.datetime.combine(event.date_from, dt.time(15), tzinfo=event.tz)
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                room=rooms[0],
                slot_type=slot_type,
                description=description,
                start=start,
                end=start + dt.timedelta(minutes=15),
            )
        freeze_schedule(event.wip_schedule, "1.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    assert [name for name in files if name.startswith("days/")] == day_files
    overview = BeautifulSoup(files["index.html"], "html.parser")
    for offset, name in enumerate(day_files):
        page = BeautifulSoup(files[name], "html.parser")
        assert len(page.select("section[data-day]")) == 1
        assert (
            page.select_one("section[data-day] h2 time")["datetime"]
            == (event.date_from + dt.timedelta(days=offset)).isoformat()
        )
        sessions = deepcopy(
            page.select('article:is([data-kind="session"], [data-kind="break"])')
        )
        for session in sessions:
            for link in session.find_all("a", href=True):
                link["href"] = link["href"].removeprefix("../")
        assert sessions == overview.select("section[data-day]")[offset].select(
            'article:is([data-kind="session"], [data-kind="break"])'
        )
        assert page.select_one('[data-field="schedule-version"] dd').get_text() == "1.0"
        assert not page.find(["script", "form", "iframe"])
    first = BeautifulSoup(files[day_files[0]], "html.parser")
    second = BeautifulSoup(files[day_files[1]], "html.parser")
    assert [
        title.get_text(strip=True)
        for title in first.select(
            'article:is([data-kind="session"], [data-kind="break"]) h3'
        )
    ] == ["Earlier session", "First day session", "Tea break", "Late session"]
    assert [
        title.get_text(strip=True)
        for title in second.select(
            'article:is([data-kind="session"], [data-kind="break"]) h3'
        )
    ] == ["Second day session"]
    assert "Room A" in first.get_text()
    assert "Room B" in first.get_text()
    assert "02:00" in first.get_text()
    assert "Private blocker" not in first.get_text()
    for name in day_files[2:]:
        assert BeautifulSoup(files[name], "html.parser").select_one(
            '[data-state="no-sessions"]'
        )


def test_daily_overviews_use_only_the_current_published_schedule(
    published_talk_slot, event, day_files
):
    with scope(event=event):
        event.wip_schedule.talks.update(room=None)
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        event.wip_schedule.talks.update(room=published_talk_slot.room)
        files = StaticScheduleExporter(event).get_files()

    for name in day_files:
        page = BeautifulSoup(files[name], "html.parser")
        assert page.select_one('[data-field="schedule-version"] dd').get_text() == "2.0"
        assert not page.select('article:is([data-kind="session"], [data-kind="break"])')
        assert page.select_one('[data-state="no-sessions"]')


@pytest.mark.parametrize("deployment", ("/", "/conference/2026/"))
def test_daily_overview_links_and_assets_work_after_extraction(
    image_uploads, event, day_files, tmp_path, deployment
):
    with scope(event=event):
        _, content_type, data = StaticScheduleExporter(event).render(request=None)
    assert content_type == "application/zip"
    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(tmp_path)
    for name in day_files:
        page = BeautifulSoup((tmp_path / name).read_text(), "html.parser")
        assert page.find("link", href="../assets/schedule.css")
        assert page.find("link", href="../assets/variables.css")
        for element in page.select("[href], [src]"):
            reference = element.get("href", element.get("src"))
            if urlsplit(reference).scheme:
                assert reference in {event.urls.schedule.full(), "https://example.org"}
                continue
            assert not reference.startswith("/")
            resolved = urlsplit(
                urljoin(f"https://static.example.org{deployment}{name}", reference)
            )
            assert resolved.path.startswith(deployment)
            assert (
                tmp_path / unquote(resolved.path.removeprefix(deployment))
            ).is_file()
    first = BeautifulSoup((tmp_path / day_files[0]).read_text(), "html.parser")
    assert (
        first.select_one('article:is([data-kind="session"], [data-kind="break"]) h3 a')[
            "href"
        ]
        == "../sessions/TOOLS01.html"
    )
    assert (
        first.select_one(
            'article[data-kind="session"] > [data-rel="speakers"] > li > a'
        )["href"]
        == "../speakers/ADA0001.html"
    )


def test_schedule_template_override_is_used_for_daily_overviews(
    published_talk_slot, event, day_files, settings, tmp_path
):
    templates = deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"].insert(0, tmp_path)
    settings.TEMPLATES = templates
    override = tmp_path / "pretalx_static_schedule/schedule.html"
    override.parent.mkdir()
    override.write_text(
        '{% extends "pretalx_static_schedule/schedule.html" %}'
        "{% block content %}{{ block.super }}<p>Custom daily schedule</p>{% endblock %}"
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()
    for name in ("index.html", *day_files):
        page = BeautifulSoup(files[name], "html.parser")
        assert "Custom daily schedule" in page.get_text()
    assert (
        "Building <tools>"
        in BeautifulSoup(files[day_files[0]], "html.parser").get_text()
    )


@pytest.mark.parametrize("timezone", ("Europe/Berlin", "Pacific/Auckland"))
def test_daily_overviews_have_date_titles_and_overview_links(
    published_talk_slot, event, day_files, timezone
):
    with scope(event=event):
        event.timezone = timezone
        event.save(update_fields=["timezone"])
        files = StaticScheduleExporter(Event.objects.get(pk=event.pk)).get_files()
    overview = BeautifulSoup(files["index.html"], "html.parser")
    assert [
        link["href"] for link in overview.select("section[data-day] h2 a")
    ] == day_files
    for offset, name in enumerate(day_files):
        page = BeautifulSoup(files[name], "html.parser")
        date = event.date_from + dt.timedelta(days=offset)
        assert (
            " ".join(page.title.get_text().split())
            == f"{date.isoformat()} – {event.name}"
        )
        assert page.select_one('header [data-field="event"]')["href"] == "../index.html"
        assert page.select_one("section[data-day] h2 a")["href"] == f"../{name}"
        assert page.select_one('header [data-field="event"]').get_text() == str(
            event.name
        )
        assert page.main.h1.time["datetime"] == date.isoformat()

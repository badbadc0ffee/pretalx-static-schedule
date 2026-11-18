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
def test_room_pages_group_published_sessions_and_breaks_by_schedule_day(
    event, day_files, timezone
):
    with scope(event=event):
        event.timezone = timezone
        event.save(update_fields=["timezone"])
        event = Event.objects.get(pk=event.pk)
        main = Room.objects.create(event=event, name="Room A", position=2)
        other = Room.objects.create(event=event, name="Room B", position=1)
        empty = Room.objects.create(event=event, name="Empty room", position=0)
        tracks = [
            Track.objects.create(event=event, name=name) for name in ("Tools", "Art")
        ]
        submissions = {}
        for code, title, room, offset, hour, track in (
            ("FIRST01", "First session", main, 0, 10, tracks[0]),
            ("EARLY01", "Earlier session", main, 0, 9, tracks[1]),
            ("NIGHT01", "Late session", main, 1, 2, None),
            ("SECOND1", "Second day session", main, 1, 10, tracks[0]),
            ("OTHER01", "Other room session", other, 0, 11, tracks[0]),
            ("NO_ROOM", "Unscheduled session", None, 0, 13, None),
            ("OUTSIDE", "Outside event dates", empty, 10, 10, None),
        ):
            submission = Submission.objects.create(
                event=event,
                code=code,
                title=title,
                track=track,
                submission_type=event.submission_types.first(),
                state=SubmissionStates.CONFIRMED,
            )
            submissions[code] = submission
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
        start = dt.datetime.combine(event.date_from, dt.time(15), tzinfo=event.tz)
        for slot_type, description in (
            (SlotType.BREAK, "Tea break"),
            (SlotType.BLOCKER, "Private blocker"),
        ):
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                room=main,
                slot_type=slot_type,
                description=description,
                start=start,
                end=start + dt.timedelta(minutes=15),
            )
        for room, offset in ((main, 1), (other, 0)):
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=submissions["FIRST01"],
                room=room,
                start=start + dt.timedelta(days=offset, hours=1),
                end=start + dt.timedelta(days=offset, hours=1, minutes=30),
            )
        freeze_schedule(event.wip_schedule, "1.0", notify_speakers=False)
        event.wip_schedule.talks.update(description="Unpublished change")
        files = StaticScheduleExporter(event).get_files()

    assert {name for name in files if name.startswith("rooms/")} == {
        f"rooms/{room.pk}.html" for room in (main, other, empty)
    }
    page = BeautifulSoup(files[f"rooms/{main.pk}.html"], "html.parser")
    assert page.h1.get_text() == "Room A"
    groups = page.select(".schedule-day")
    assert len(groups) == 2
    assert [group.select_one("h2 a")["href"] for group in groups] == [
        f"../{name}" for name in day_files[:2]
    ]
    assert [
        title.get_text(strip=True) for title in groups[0].select(".session h3")
    ] == ["Earlier session", "First session", "Tea break", "Late session"]
    assert [
        title.get_text(strip=True) for title in groups[1].select(".session h3")
    ] == ["Second day session", "First session"]
    assert "02:00" in groups[0].get_text()
    assert {label.get_text(strip=True) for label in page.select(".session .track")} == {
        "Tools",
        "Art",
    }
    for excluded in (
        "Other room session",
        "Unscheduled session",
        "Private blocker",
        "Unpublished change",
    ):
        assert excluded not in page.get_text()
    other_page = BeautifulSoup(files[f"rooms/{other.pk}.html"], "html.parser")
    assert [title.get_text() for title in other_page.select(".session h3 a")] == [
        "Other room session",
        "First session",
    ]
    assert not page.select(".session h3 a[href$='OUTSIDE.html']")
    empty_page = BeautifulSoup(files[f"rooms/{empty.pk}.html"], "html.parser")
    assert "No sessions in this room." in empty_page.get_text()
    assert not empty_page.select(".session, .schedule-day")
    for name, prefix in (("index.html", ""), (day_files[0], "../")):
        overview = BeautifulSoup(files[name], "html.parser")
        assert [
            (link.get_text(), link["href"])
            for link in overview.select('[data-group="rooms"] a')
        ] == [
            (room.name, f"{prefix}rooms/{room.pk}.html")
            for room in (empty, other, main)
        ]


def test_room_pages_use_only_the_current_published_schedule(published_talk_slot, event):
    with scope(event=event):
        event.wip_schedule.talks.update(room=None)
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        event.wip_schedule.talks.update(room=published_talk_slot.room)
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(
        files[f"rooms/{published_talk_slot.room_id}.html"], "html.parser"
    )
    assert page.select_one('[data-field="schedule-version"] dd').get_text() == "2.0"
    assert "No sessions in this room." in page.get_text()
    assert not page.select(".session")


@pytest.mark.parametrize("deployment", ("/", "/conference/2026/"))
def test_room_links_and_assets_work_after_extraction(
    image_uploads, published_talk_slot, event, day_files, tmp_path, deployment
):
    with scope(event=event):
        submission = published_talk_slot.submission
        other = Submission.objects.create(
            event=event,
            code="OTHER01",
            title="Another session",
            submission_type=submission.submission_type,
            state=SubmissionStates.CONFIRMED,
        )
        other.speakers.add(*submission.sorted_speakers)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=other,
            room=published_talk_slot.room,
            start=published_talk_slot.start + dt.timedelta(days=1),
            end=published_talk_slot.end + dt.timedelta(days=1),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        _, _, data = StaticScheduleExporter(event).render(request=None)
    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(tmp_path)

    room_file = f"rooms/{published_talk_slot.room_id}.html"
    for name, prefix in (
        ("index.html", ""),
        (day_files[0], "../"),
        (f"tracks/{submission.track_id}.html", "../"),
        ("sessions/TOOLS01.html", "../"),
        ("speakers/ADA0001.html", "../"),
        (room_file, "../"),
    ):
        page = BeautifulSoup((tmp_path / name).read_text(), "html.parser")
        room_links = page.select("a.room, .room a")
        assert room_links
        assert {link["href"] for link in room_links} == {prefix + room_file}
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
    page = BeautifulSoup((tmp_path / room_file).read_text(), "html.parser")
    assert page.header.find("a")["href"] == "../index.html"
    assert page.select_one(".session h3 a")["href"] == "../sessions/TOOLS01.html"
    assert page.find("a", href="../speakers/ADA0001.html")
    assert not page.find(["script", "form", "iframe"])
    detail = BeautifulSoup(
        (tmp_path / "sessions/TOOLS01.html").read_text(), "html.parser"
    )
    assert detail.select_one(".speaker-sessions a.room")["href"] == "../" + room_file


@pytest.mark.parametrize("absolute", (False, True))
@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net"))
def test_room_descriptions_names_and_links(
    published_talk_slot, event, absolute, custom_domain
):
    with scope(event=event):
        event.custom_domain = custom_domain
        event.save(update_fields=["custom_domain"])
        submission = published_talk_slot.submission
        speaker = submission.sorted_speakers.first()
        room = published_talk_slot.room
        room.name = "Room & <practice> / ../"
        room.speaker_info = "Private room setup"
        room.description = "**Main hall**: " + " · ".join(
            f"[{label}]({url.full() if absolute else url})"
            for label, url in (
                ("Schedule", event.urls.schedule),
                ("Session", submission.urls.public),
                ("Speaker", speaker.urls.public),
            )
        )
        room.save(update_fields=["name", "description", "speaker_info"])
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files[f"rooms/{room.pk}.html"], "html.parser")
    assert page.h1.get_text() == room.name
    assert page.title.get_text(strip=True) == f"{room.name} – {event.name}"
    assert page.select_one(".description strong").get_text() == "Main hall"
    assert [
        (link.get_text(), link["href"]) for link in page.select(".description a")
    ] == [
        ("Schedule", "../index.html"),
        ("Session", "../sessions/TOOLS01.html"),
        ("Speaker", "../speakers/ADA0001.html"),
    ]
    assert not page.find("practice")
    assert "Private room setup" not in page.get_text()


def test_room_template_can_extend_the_default(
    published_talk_slot, event, settings, tmp_path
):
    templates = deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"].insert(0, tmp_path)
    settings.TEMPLATES = templates
    override = tmp_path / "pretalx_static_schedule/room.html"
    override.parent.mkdir()
    override.write_text(
        '{% extends "pretalx_static_schedule/room.html" %}'
        "{% block content %}{{ block.super }}<p>Custom room note</p>{% endblock %}"
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(
        files[f"rooms/{published_talk_slot.room_id}.html"], "html.parser"
    )
    assert "Custom room note" in page.get_text()
    assert page.h1.get_text() == "Room A"
    assert len(page.select(".session")) == 1

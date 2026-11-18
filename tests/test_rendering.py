import datetime as dt
from io import BytesIO
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.utils.translation import get_language, override
from django_scopes import scope

from pretalx.person.models import SpeakerProfile
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.enums import SlotType
from pretalx.schedule.models import Room, TalkSlot
from pretalx.submission.models import (
    Answer,
    Question,
    Resource,
    Submission,
    SubmissionStates,
    Tag,
    Track,
)

from pretalx_static_schedule import __version__
from pretalx_static_schedule import exporter as exporter_module
from pretalx_static_schedule.exporter import StaticScheduleExporter
from pretalx_static_schedule.rendering import (
    ScheduleRenderData,
    language_name,
    omit_backlink,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("duration", (30, None))
def test_published_slot_without_end_uses_the_planned_duration_in_every_view(
    published_talk_slot, event, duration
):
    with scope(event=event):
        submission = published_talk_slot.submission
        submission.duration = duration
        submission.save(update_fields=["duration"])
        submission.submission_type.default_duration = 35
        submission.submission_type.save(update_fields=["default_duration"])
        event.current_schedule.talks.filter(submission=submission).update(end=None)
        _, _, archive_data = StaticScheduleExporter(event).render(request=None)
        assert event.current_schedule.talks.get(submission=submission).end is None

    minutes = 35 if duration is None else duration
    expected_end = (
        published_talk_slot.start + dt.timedelta(minutes=minutes)
    ).astimezone(event.tz)
    kinds = set()
    with ZipFile(BytesIO(archive_data)) as archive:
        assert archive.testzip() is None
        for filename in archive.namelist():
            if not filename.endswith(".html"):
                continue
            page = BeautifulSoup(archive.read(filename), "html.parser")
            ends = page.main.select('[data-field="end"] time')
            if not ends:
                continue
            kinds.add(page.main["data-page"])
            assert all(time["datetime"] == expected_end.isoformat() for time in ends)
            assert all(
                time.get_text().endswith(expected_end.strftime("%H:%M"))
                for time in ends
            )
            durations = page.main.select('[data-field="duration"] data')
            assert len(durations) == len(ends)
            assert all(
                value["value"] == f"PT{minutes}M" and value.get_text() == str(minutes)
                for value in durations
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


@pytest.mark.parametrize(
    ("codes", "parent", "expected"),
    (
        ([], None, False),
        ([], ("session", "A"), False),
        (["A"], None, False),
        (["A"], ("session", "A"), True),
        (["A", "A"], ("session", "A"), True),
        (["A"], ("speaker", "A"), False),
        (["A"], ("session", "B"), False),
        (["A", "B"], ("session", "A"), False),
    ),
)
def test_backlink_omission_requires_one_distinct_matching_parent(
    codes, parent, expected
):
    assert omit_backlink("session", codes, parent) is expected


@pytest.mark.parametrize(
    ("code", "name"),
    (("de", "German"), ("en", "English"), ("x-custom", "x-custom"), ("", "")),
)
def test_language_names_are_english_with_a_fallback_for_unknown_codes(code, name):
    with override("de"):
        assert language_name(code) == name


@pytest.fixture
def rendering_data(published_talk_slot, event):
    with scope(event=event):
        first = published_talk_slot.submission
        ada = first.sorted_speakers.first()
        bob = SpeakerProfile.objects.create(
            event=event, code="BOB0001", name="Bob Example"
        )
        first.speakers.add(bob)
        room = Room.objects.create(event=event, name="Room B", position=0)
        Room.objects.create(event=event, name="Empty room")
        Track.objects.create(event=event, name="Empty track")
        night = dt.datetime.combine(
            event.date_from + dt.timedelta(days=1), dt.time(2), tzinfo=event.tz
        )
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=first,
            room=room,
            start=night,
            end=night + dt.timedelta(minutes=20),
        )
        for code, title, speakers, assigned_track, offset in (
            ("OTHER01", "Another presentation", [ada], first.track, 1),
            ("NONE001", "Untracked and speakerless", [], None, 2),
        ):
            submission = Submission.objects.create(
                event=event,
                code=code,
                title=title,
                submission_type=first.submission_type,
                state=SubmissionStates.CONFIRMED,
                track=assigned_track,
            )
            submission.speakers.add(*speakers)
            start = published_talk_slot.start + dt.timedelta(hours=offset)
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=submission,
                room=room,
                start=start,
                end=start + dt.timedelta(minutes=30),
            )
        start = published_talk_slot.start + dt.timedelta(hours=3)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            slot_type=SlotType.BREAK,
            description="Tea break",
            room=room,
            start=start,
            end=start + dt.timedelta(minutes=15),
        )
        first.tags.add(
            Tag.objects.create(event=event, tag="Public", is_public=True),
            Tag.objects.create(event=event, tag="Private", is_public=False),
        )
        for public in (True, False):
            for target, owner in (
                ("speaker", {"speaker": ada}),
                ("submission", {"submission": first}),
            ):
                question = Question.objects.create(
                    event=event,
                    question="Public" if public else "Private",
                    target=target,
                    is_public=public,
                )
                Answer.objects.create(question=question, answer="Answer", **owner)
            Resource.objects.create(
                submission=first,
                description="Public" if public else "Private",
                is_public=public,
                link="https://example.org/resource",
            )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        hidden = Submission.objects.create(
            event=event,
            code="HIDDEN1",
            title="WIP only",
            submission_type=first.submission_type,
            state=SubmissionStates.CONFIRMED,
        )
        hidden.speakers.add(
            SpeakerProfile.objects.create(
                event=event, code="HIDDEN1", name="Hidden speaker"
            )
        )
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=hidden,
            room=room,
            start=start + dt.timedelta(days=1),
            end=start + dt.timedelta(days=1, minutes=30),
        )
        return ScheduleRenderData(event, event.current_schedule)


def test_rendering_data_indexes_only_published_entities_and_actual_occurrences(
    rendering_data, event
):
    data = rendering_data
    assert set(data.sessions) == {"TOOLS01", "OTHER01", "NONE001"}
    assert set(data.speakers) == {"ADA0001", "BOB0001"}
    assert len(data.occurrences) == 5
    assert len(data.sessions["TOOLS01"]["occurrences"]) == 2
    assert data.speakers["ADA0001"]["submission_count"] == 2
    assert data.speakers["BOB0001"]["submission_count"] == 1
    assert [speaker["name"] for speaker in data.sorted_speakers] == [
        "Ada Example",
        "Bob Example",
    ]
    night = data.sessions["TOOLS01"]["occurrences"][1]
    assert night["start"].date() == event.date_from + dt.timedelta(days=1)
    assert night["day"] == event.date_from.isoformat()
    assert night["duration_iso"] == "PT20M"
    assert night["room_name"] == "Room B"
    assert night["attributes"] == {
        "data-kind": "session",
        "data-session": "TOOLS01",
        "data-slot": str(night["id"]),
        "data-room": str(night["room_id"]),
        "data-track": str(night["track_id"]),
        "data-day": event.date_from.isoformat(),
    }
    untracked = data.sessions["NONE001"]
    assert "data-track" not in untracked["attributes"]
    assert not untracked["speaker_codes"]
    pause = next(
        item for item in data.occurrences.values() if item["session_code"] is None
    )
    assert pause["title"] == "Tea break"
    assert pause["attributes"]["data-kind"] == "break"
    assert "data-session" not in pause["attributes"]
    assert "data-track" not in pause["attributes"]


def test_prepared_fields_contain_only_public_answers_tags_and_resources(
    rendering_data, event
):
    with scope(event=event):
        session = rendering_data.sessions["TOOLS01"]
        speaker = rendering_data.speakers["ADA0001"]
        assert [str(answer.question.question) for answer in session["answers"]] == [
            "Public"
        ]
        assert [str(answer.question.question) for answer in speaker["answers"]] == [
            "Public"
        ]
        assert [resource.description for resource in session["files"]] == ["Public"]
        assert [tag.tag for tag in session["tags"]] == ["Public"]
        assert "notes" not in session
        assert "internal_notes" not in speaker
        assert speaker["language"] is None


def test_related_views_expand_one_level_then_stop_at_references(rendering_data):
    data = rendering_data
    session = data.session_view("TOOLS01", view="detail")
    ada, bob = session["speakers"]
    assert session["attributes"]["data-view"] == "detail"
    assert ada["relationship_mode"] == bob["relationship_mode"] == "references"
    assert {item["code"] for item in ada["presentations"]} == {"TOOLS01", "OTHER01"}
    assert len(ada["presentations"]) == 3
    assert not bob["presentations"]
    assert all("speakers" not in item for item in ada["presentations"])
    speaker = data.speaker_view("ADA0001", view="detail")
    assert len(speaker["presentations"]) == 3
    for presentation in speaker["presentations"]:
        assert presentation["relationship_mode"] == "references"
        assert len(presentation["occurrences"]) == 1
        if presentation["code"] == "TOOLS01":
            assert [item["code"] for item in presentation["speakers"]] == [
                "ADA0001",
                "BOB0001",
            ]
            assert all("presentations" not in item for item in presentation["speakers"])
        else:
            assert not presentation["speakers"]
    assert len(data.speaker_view("BOB0001")["presentations"]) == 2
    assert (
        len(
            data.speaker_view("BOB0001", parent=("session", "OTHER01"), expand=False)[
                "presentations"
            ]
        )
        == 2
    )
    assert (
        data.session_view("OTHER01", parent=("speaker", "BOB0001"), expand=False)[
            "speakers"
        ][0]["code"]
        == "ADA0001"
    )


def test_all_page_views_are_prepared_without_database_queries_or_shared_state_changes(
    rendering_data, event, django_assert_num_queries
):
    data = rendering_data
    with scope(event=event), django_assert_num_queries(0):
        pages = list(data.pages())
    assert {page["kind"] for page in pages} == {
        "sessions",
        "day",
        "track",
        "room",
        "session",
        "speaker",
        "speakers",
    }
    destinations = {
        item["filename"] for group in data.navigation for item in group["items"]
    }
    for page in pages:
        current = [
            item["filename"]
            for group in page["navigation"]
            for item in group["items"]
            if item["is_current"]
        ]
        assert current == (
            [page["filename"]] if page["filename"] in destinations else []
        )
        assert page["attributes"]["data-page"] == page["kind"]
        assert page["asset_prefix"] == (
            "" if page["filename"] == "index.html" else "../"
        )
        if page["kind"] in {"track", "room"}:
            field = "track_id" if page["kind"] == "track" else "room_id"
            expected = page[page["kind"]].pk
            assert all(
                occurrence[field] == expected
                for day in page["days"]
                for occurrence in day["occurrences"]
            )
    assert any(not page["groups"] for page in pages if page["kind"] == "track")
    assert all(
        "is_current" not in item for group in data.navigation for item in group["items"]
    )
    assert all("data-view" not in item["attributes"] for item in data.sessions.values())
    assert all("data-view" not in item["attributes"] for item in data.speakers.values())
    assert data.metadata["exporter_version"] == __version__
    assert data.metadata["schedule_version"] == "2.0"


@pytest.mark.parametrize("locale", ("de", "fr"))
def test_export_interface_is_english_and_preserves_authored_content(
    published_talk_slot, event, locale
):
    with scope(event=event):
        submission = published_talk_slot.submission
        submission.title = "Werkzeuge für den Alltag"
        submission.abstract = "Ein deutscher Abstract."
        submission.content_locale = "de"
        submission.do_not_record = True
        submission.save(
            update_fields=["title", "abstract", "content_locale", "do_not_record"]
        )
        speaker = submission.sorted_speakers.first()
        speaker.biography = "Une biographie française."
        speaker.save(update_fields=["biography"])
        with override(locale):
            files = StaticScheduleExporter(event).get_files()
            assert get_language() == locale
    for name, content in files.items():
        if name.endswith(".html"):
            page = BeautifulSoup(content, "html.parser")
            assert page.html["lang"] == "en"
            assert page.select_one('[data-group="days"]')["aria-label"] == "Days"
    session = BeautifulSoup(files["sessions/TOOLS01.html"], "html.parser")
    assert session.h1.get_text() == submission.title
    assert submission.abstract in session.get_text()
    assert speaker.biography in session.get_text()
    assert session.select_one('[data-field="language"] data').get_text() == "German"
    assert (
        session.select_one('[data-state="recording-forbidden"]')["aria-label"]
        == "This session will not be recorded."
    )


def test_export_restores_the_active_locale_on_failure(event):
    with scope(event=event), override("de"):
        with pytest.raises(ValueError, match="No published schedule"):
            StaticScheduleExporter(event).get_files()
        assert get_language() == "de"


def test_prepared_page_titles_keep_the_english_translation_when_the_locale_changes(
    published_talk_slot, event
):
    with scope(event=event):
        event.name = {"en": "Example event", "de": "Beispielveranstaltung"}
        event.save(update_fields=["name"])
        event.refresh_from_db()
        track = published_talk_slot.submission.track
        track.name = {"en": "Tools", "de": "Werkzeuge"}
        track.save(update_fields=["name"])
        room = published_talk_slot.room
        room.name = {"en": "Main hall", "de": "Hauptsaal"}
        room.save(update_fields=["name"])
        with override("de"):
            data = ScheduleRenderData(event, event.current_schedule)
            pages = list(data.pages())
            assert get_language() == "de"
    assert data.metadata["title"] == "Example event"
    assert next(page["title"] for page in pages if page["kind"] == "track") == "Tools"
    assert (
        next(page["title"] for page in pages if page["kind"] == "room") == "Main hall"
    )
    groups = {group["kind"]: group for group in data.navigation}
    assert groups["tracks"]["items"][0]["label"] == "Tools"
    assert groups["rooms"]["items"][0]["label"] == "Main hall"


def test_templates_receive_prepared_page_data(published_talk_slot, event, mocker):
    render = mocker.spy(exporter_module, "render_to_string")
    with scope(event=event):
        StaticScheduleExporter(event).get_files()
    contexts = {
        call.args[1]["page"]["filename"]: call.args[1]
        for call in render.call_args_list
        if "page" in call.args[1]
    }
    detail = contexts["sessions/TOOLS01.html"]
    assert detail["page"]["item"]["code"] == "TOOLS01"
    assert detail["session"]["language_name"] == "English"
    assert not detail["page"]["item"]["speakers"][0]["presentations"]
    overview = contexts["speakers/index.html"]
    entry = overview["page"]["items"][0]
    assert entry["relationship_mode"] == "references"
    assert entry["presentations"][0]["code"] == "TOOLS01"
    assert "speakers" not in entry["presentations"][0]
    assert overview["metadata"]["exporter_version"] == __version__
    assert all("navigation_days" not in context for context in contexts.values())

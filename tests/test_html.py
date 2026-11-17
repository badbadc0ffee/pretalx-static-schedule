import datetime as dt
from urllib.parse import quote

import pytest
from bs4 import BeautifulSoup
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test.html import parse_html
from django_scopes import scope
from pytest_django.asserts import assertInHTML

from pretalx.event.models import Event
from pretalx.person.models import AttendeeProfile
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.enums import SlotType
from pretalx.schedule.models import Room, TalkSlot
from pretalx.submission.models import (
    Answer,
    AnswerOption,
    AttendeeSignup,
    Question,
    Resource,
    Submission,
    SubmissionStates,
    Track,
)

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def test_empty_published_schedule(published_schedule, event, day_files):
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert list(files) == [
        "assets/schedule.css",
        "assets/variables.css",
        "index.html",
        *day_files,
    ]
    html = files["index.html"].decode("utf-8")
    assertInHTML(f"<h1>{event.name}</h1>", html)
    assert "Schedule version 1.0" in html
    assert html.count("No sessions on") == event.duration


def test_schedule_overview(published_talk_slot, event):
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    html = files["index.html"].decode("utf-8")
    assertInHTML('<a href="sessions/TOOLS01.html">Building &lt;tools&gt;</a>', html)
    assertInHTML(
        f'<time datetime="{published_talk_slot.start.isoformat()}">10:00</time>', html
    )
    assert "45 min" in html
    assert "Room A" in html
    assert "Ada Example" in html
    assert "Tools" in html
    assert "Europe/Berlin" in html
    assertInHTML("<p>A <strong>small</strong> tool with a project.</p>", html)
    assert "Private organiser notes" not in html


def test_session_detail(published_talk_slot, event):
    with scope(event=event):
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assertInHTML("<h1>Building &lt;tools&gt;</h1>", html)
    assertInHTML(f'<a href="../index.html">{event.name}</a>', html)
    assertInHTML(
        f'<time datetime="{published_talk_slot.start.isoformat()}">10:00</time>', html
    )
    assertInHTML(
        f'<time datetime="{published_talk_slot.end.isoformat()}">10:45</time>', html
    )
    assert "45 min" in " ".join(html.split())
    assert "Room A" in html
    assertInHTML(
        f'<a class="room" href="../rooms/{published_talk_slot.room_id}.html" title="Main hall">Room A</a>',
        html,
    )
    assert "Ada Example" in html
    assertInHTML("<p>Builds <strong>small tools</strong>.</p>", html)
    assert "A longer description of the tool." in html
    assert 'href="https://example.org"' in html
    assert "/redirect/" not in html
    assert "Private organiser notes" not in html


def test_public_answers_and_resources(published_talk_slot, event):
    with scope(event=event):
        submission = published_talk_slot.submission
        for variant, label, value in (
            ("text", "Public field", "A **public** answer"),
            ("url", "Project URL", "https://example.org/project"),
            ("boolean", "Available", "True"),
            ("boolean", "Needs equipment", "False"),
            ("choices", "Format", ""),
        ):
            question = Question.objects.create(
                event=event, question=label, variant=variant, is_public=True
            )
            answer = Answer.objects.create(
                submission=submission, question=question, answer=value
            )
            if variant == "choices":
                answer.options.add(
                    AnswerOption.objects.create(question=question, answer="Workshop")
                )
        private = Question.objects.create(
            event=event, question="Private field", is_public=False
        )
        Answer.objects.create(
            submission=submission, question=private, answer="Private answer"
        )
        restricted = Question.objects.create(
            event=event, question="Other track field", is_public=True
        )
        restricted.tracks.add(Track.objects.create(event=event, name="Other track"))
        Answer.objects.create(
            submission=submission, question=restricted, answer="Other track answer"
        )
        Resource.objects.create(
            submission=submission,
            link="https://example.org/slides",
            description="Slides",
            is_public=True,
        )
        Resource.objects.create(
            submission=submission,
            link="https://example.org/private",
            description="Private slides",
            is_public=False,
        )
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assert "Public field" in html
    assertInHTML("<p>A <strong>public</strong> answer</p>", html)
    assert 'href="https://example.org/project"' in html
    assertInHTML("<dd><p>Yes</p></dd>", html)
    assertInHTML("<dd><p>No</p></dd>", html)
    assert "Workshop" in html
    assertInHTML('<a href="https://example.org/slides">Slides</a>', html)
    assert "Private field" not in html
    assert "Private answer" not in html
    assert "Other track answer" not in html
    assert "Private slides" not in html


def test_public_file_metadata(published_talk_slot, event):
    with scope(event=event):
        submission = published_talk_slot.submission
        for label, upload in (
            ("Public document", SimpleUploadedFile("document.txt", b"Document")),
            ("Missing document", None),
        ):
            question = Question.objects.create(
                event=event, question=label, variant="file", is_public=True
            )
            Answer.objects.create(
                submission=submission, question=question, answer="", answer_file=upload
            )
        Resource.objects.create(
            submission=submission,
            resource=SimpleUploadedFile("handout.txt", b"Handout"),
            description="Handout",
            is_public=True,
        )
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

        document = submission.answers.get(question__question="Public document")
        online_answer = render_to_string(
            "common/question_answer.html", {"answer": document}
        )

    assert "Public document" in html
    assert str(document.answer_file) in online_answer
    assertInHTML(
        f'<a href="../media/{quote(document.answer_file.name)}">{document.answer_file}</a>',
        html,
    )
    assert "Missing document" in html
    assert "No file provided" in html
    assert "Handout" in html


def test_unanswered_public_field(published_talk_slot, event):
    with scope(event=event):
        question = Question.objects.create(
            event=event, question="Unanswered field", is_public=True
        )
        Answer.objects.create(
            submission=published_talk_slot.submission, question=question, answer=""
        )
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assert "Unanswered field" in html
    assert "No response" in html


def test_language_and_recording_flag(published_talk_slot, event):
    with scope(event=event):
        event.content_locales = ["en", "de"]
        event.save(update_fields=["content_locales"])
        submission = published_talk_slot.submission
        submission.do_not_record = True
        submission.save(update_fields=["do_not_record"])
        files = StaticScheduleExporter(Event.objects.get(pk=event.pk)).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    assertInHTML("<p><strong>Language:</strong> English</p>", html)
    for name in ("index.html", "sessions/TOOLS01.html", "speakers/ADA0001.html"):
        assert "This session will not be recorded." in files[name].decode("utf-8")


@pytest.mark.parametrize("request_content_locale", (True, False))
@pytest.mark.parametrize(
    ("content_locales", "content_locale", "language"),
    ((("en",), "en", "English"), (("en", "de"), "de", "Deutsch")),
)
def test_session_content_language_is_always_exported(
    published_talk_slot,
    event,
    request_content_locale,
    content_locales,
    content_locale,
    language,
):
    with scope(event=event):
        event.content_locales = list(content_locales)
        event.save(update_fields=["content_locales"])
        event.cfp.fields["content_locale"]["visibility"] = (
            "required" if request_content_locale else "do_not_ask"
        )
        event.cfp.save(update_fields=["fields"])
        submission = published_talk_slot.submission
        submission.content_locale = content_locale
        submission.save(update_fields=["content_locale"])
        html = (
            StaticScheduleExporter(Event.objects.get(pk=event.pk))
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assertInHTML(f"<p><strong>Language:</strong> {language}</p>", html)


@pytest.mark.parametrize("use_tracks", (True, False))
@pytest.mark.parametrize("has_track", (True, False))
def test_session_track_is_exported_when_assigned(
    published_talk_slot, event, use_tracks, has_track
):
    with scope(event=event):
        event.feature_flags["use_tracks"] = use_tracks
        event.save(update_fields=["feature_flags"])
        event.cfp.fields["track"]["visibility"] = "do_not_ask"
        event.cfp.save(update_fields=["fields"])
        submission = published_talk_slot.submission
        if has_track:
            submission.track.name = "Tools & <practice>"
            submission.track.save(update_fields=["name"])
        else:
            submission.track = None
            submission.save(update_fields=["track"])
        html = (
            StaticScheduleExporter(Event.objects.get(pk=event.pk))
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    if has_track:
        assertInHTML(
            f'<p class="track"><strong>Track:</strong> <a href="../tracks/{submission.track_id}.html">Tools &amp; &lt;practice&gt;</a></p>',
            html,
        )
    else:
        assert "Track:" not in html


@pytest.mark.parametrize(
    ("full", "message"),
    (
        (False, "This session requires attendees to sign up."),
        (True, "This session is currently full."),
    ),
)
def test_signup_information(published_talk_slot, event, orga_user, full, message):
    with scope(event=event):
        event.feature_flags["attendee_signup"] = True
        event.save(update_fields=["feature_flags"])
        submission = published_talk_slot.submission
        submission.attendee_signup_required = True
        submission.attendee_signup_capacity = 1
        submission.save(
            update_fields=["attendee_signup_required", "attendee_signup_capacity"]
        )
        if full:
            AttendeeSignup.objects.create(
                submission=submission,
                attendee=AttendeeProfile.objects.create(event=event, user=orga_user),
            )
        files = StaticScheduleExporter(event).get_files()

    for name in ("index.html", "sessions/TOOLS01.html", "speakers/ADA0001.html"):
        html = files[name].decode("utf-8")
        assert message in html
        assert "<form" not in html
        assert "Orga User" not in html


def test_other_speaker_appearances(published_talk_slot, event):
    with scope(event=event):
        speaker = published_talk_slot.submission.sorted_speakers.first()
        for code, title, shared_speaker in (
            ("OTHER01", "Other appearance", True),
            ("UNREL01", "Unrelated appearance", False),
        ):
            submission = Submission.objects.create(
                event=event,
                code=code,
                title=title,
                submission_type=event.submission_types.first(),
                state=SubmissionStates.CONFIRMED,
            )
            if shared_speaker:
                submission.speakers.add(speaker)
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=submission,
                room=published_talk_slot.room,
                start=published_talk_slot.end,
                end=published_talk_slot.end + dt.timedelta(minutes=30),
            )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assertInHTML('<a href="OTHER01.html">Other appearance</a>', html)
    assert "Unrelated appearance" not in html


def test_repeated_session_has_one_detail_page(published_talk_slot, event):
    with scope(event=event):
        second_start = published_talk_slot.start + dt.timedelta(days=1)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=published_talk_slot.submission,
            room=Room.objects.create(event=event, name="Room B"),
            start=second_start,
            end=second_start + dt.timedelta(minutes=15),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    assert [name for name in files if name.startswith("sessions/")] == [
        "sessions/TOOLS01.html"
    ]
    html = files["sessions/TOOLS01.html"].decode("utf-8")
    assert "Room A" in html
    assert "Room B" in html
    assertInHTML(f'<time datetime="{second_start.isoformat()}">10:00</time>', html)
    assert "15 min" in " ".join(html.split())


@pytest.mark.parametrize("hidden_reason", ("unconfirmed", "no_room", "no_start"))
def test_hidden_or_unscheduled_sessions_are_not_exported(
    published_talk_slot, event, hidden_reason
):
    with scope(event=event):
        submission = Submission.objects.create(
            event=event,
            code="HIDDEN1",
            title="Hidden session",
            submission_type=event.submission_types.first(),
            state=SubmissionStates.SUBMITTED
            if hidden_reason == "unconfirmed"
            else SubmissionStates.CONFIRMED,
        )
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=submission,
            room=None if hidden_reason == "no_room" else published_talk_slot.room,
            start=None if hidden_reason == "no_start" else published_talk_slot.end,
            end=published_talk_slot.end + dt.timedelta(minutes=30),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    assert "sessions/HIDDEN1.html" not in files
    assert "Hidden session" not in files["index.html"].decode("utf-8")
    assert all(
        "Hidden session" not in BeautifulSoup(content, "html.parser").get_text()
        for name, content in files.items()
        if name.startswith("rooms/")
    )
    assert "sessions/TOOLS01.html" in files


def test_multiple_days_and_rooms(published_talk_slot, event):
    with scope(event=event):
        room = Room.objects.create(event=event, name="Room B")
        for code, title, start in (
            (
                "EARLY01",
                "Earlier session",
                published_talk_slot.start - dt.timedelta(hours=1),
            ),
            (
                "DAYTWO1",
                "Second day session",
                published_talk_slot.start + dt.timedelta(days=1),
            ),
        ):
            submission = Submission.objects.create(
                event=event,
                code=code,
                title=title,
                submission_type=event.submission_types.first(),
                state=SubmissionStates.CONFIRMED,
            )
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=submission,
                room=room,
                start=start,
                end=start + dt.timedelta(minutes=30),
            )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        html = StaticScheduleExporter(event).get_files()["index.html"].decode("utf-8")

    assert html.index("Earlier session") < html.index("Building &lt;tools&gt;")
    assert html.index("Building &lt;tools&gt;") < html.index("Second day session")
    assert "Room A" in html
    assert "Room B" in html
    assert f'datetime="{(event.date_from + dt.timedelta(days=1)).isoformat()}"' in html


def test_breaks_are_visible_and_blockers_are_not(published_talk_slot, event):
    with scope(event=event):
        for slot_type, description in (
            (SlotType.BREAK, "Tea break"),
            (SlotType.BLOCKER, "Private blocker"),
        ):
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                room=published_talk_slot.room,
                slot_type=slot_type,
                description=description,
                start=published_talk_slot.end,
                end=published_talk_slot.end + dt.timedelta(minutes=15),
            )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        html = StaticScheduleExporter(event).get_files()["index.html"].decode("utf-8")

    assert "Tea break" in html
    assert "Private blocker" not in html


def test_pages_use_published_times_and_rooms(published_talk_slot, event):
    with scope(event=event):
        room = Room.objects.create(event=event, name="WIP room")
        event.wip_schedule.talks.update(
            room=room, start=published_talk_slot.start + dt.timedelta(hours=4)
        )
        files = StaticScheduleExporter(event).get_files()

    for name in ("index.html", "sessions/TOOLS01.html"):
        html = files[name].decode("utf-8")
        page = BeautifulSoup(html, "html.parser")
        assert {
            room.get_text(strip=True)
            for room in page.select(".session .room, .session-times .room")
        } == {"Room A"}
        assertInHTML(
            f'<time datetime="{published_talk_slot.start.isoformat()}">10:00</time>',
            html,
        )


def test_default_html_is_valid_and_needs_no_javascript(published_talk_slot, event):
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    for name, content in files.items():
        if not name.endswith(".html"):
            continue
        html = content.decode("utf-8")
        assert parse_html(html)
        assert "<!doctype html>" in html.lower()
        assert "<script" not in html.lower()
        assert "javascript:" not in html.lower()
    assert not any(name.endswith(".js") for name in files)


def test_no_website_without_a_published_schedule(event):
    with (
        scope(event=event),
        pytest.raises(ValueError, match="No published schedule is available"),
    ):
        StaticScheduleExporter(event).get_files()

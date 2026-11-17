import datetime as dt
from io import BytesIO
from urllib.parse import quote
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.test.html import parse_html
from django_scopes import scope
from pytest_django.asserts import assertInHTML

from pretalx.person.models import SpeakerProfile
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.models import TalkSlot
from pretalx.submission.models import Submission, SubmissionStates

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("name", ("Ada Example", "Ada & <team>"))
def test_speaker_page_uses_the_shared_layout(published_speaker, event, tmp_path, name):
    with scope(event=event):
        published_speaker.name = name
        published_speaker.save(update_fields=["name"])
        _, content_type, data = StaticScheduleExporter(event).render(request=None)

    assert content_type == "application/zip"
    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(tmp_path)
    html = (tmp_path / "speakers/ADA0001.html").read_text(encoding="utf-8")
    page = BeautifulSoup(html, "html.parser")
    assert page.find("h1").get_text() == name
    assert page.title.get_text() == f"{name} – {event.name}"
    assert page.header.find("a")["href"] == "../index.html"
    assert page.header.find("a").get_text() == str(event.name)
    assert page.find("link", rel="stylesheet")["href"] == "../assets/schedule.css"
    assert "Europe/Berlin" in page.header.get_text()
    assert parse_html(html)
    assert not page.find(["script", "form", "iframe"])


@pytest.mark.parametrize("repeated", (False, True))
def test_shared_speakers_have_one_page_each(
    published_talk_slot, published_speaker, event, repeated
):
    with scope(event=event):
        second_speaker = SpeakerProfile.objects.create(event=event, name="Ada Example")
        other = Submission.objects.create(
            event=event,
            code="OTHER01",
            title="Other session",
            submission_type=published_talk_slot.submission.submission_type,
            state=SubmissionStates.CONFIRMED,
        )
        other.speakers.add(published_speaker, second_speaker)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=other,
            room=published_talk_slot.room,
            start=published_talk_slot.end,
            end=published_talk_slot.end + dt.timedelta(minutes=30),
        )
        if repeated:
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=other,
                room=published_talk_slot.room,
                start=published_talk_slot.start + dt.timedelta(days=1),
                end=published_talk_slot.end + dt.timedelta(days=1),
            )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    assert {name for name in files if name.startswith("speakers/")} == {
        "speakers/ADA0001.html",
        f"speakers/{second_speaker.code}.html",
    }
    for speaker in (published_speaker, second_speaker):
        assertInHTML(
            "<h1>Ada Example</h1>", files[f"speakers/{speaker.code}.html"].decode()
        )


@pytest.mark.parametrize("reason", ("archived", "wip", "unscheduled", "unconfirmed"))
def test_speaker_pages_use_only_current_published_sessions(
    published_talk_slot, published_speaker, event, reason
):
    with scope(event=event):
        unused = SpeakerProfile.objects.create(event=event, name="Unused speaker")
        excluded = SpeakerProfile.objects.create(event=event, name="Excluded speaker")
        submission = Submission.objects.create(
            event=event,
            code="EXCLUDE1",
            title="Excluded session",
            submission_type=published_talk_slot.submission.submission_type,
            state=SubmissionStates.SUBMITTED
            if reason == "unconfirmed"
            else SubmissionStates.CONFIRMED,
        )
        submission.speakers.add(excluded)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=submission,
            room=None if reason == "unscheduled" else published_talk_slot.room,
            start=published_talk_slot.end,
            end=published_talk_slot.end + dt.timedelta(minutes=30),
        )
        if reason != "wip":
            freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        if reason == "archived":
            event.wip_schedule.talks.filter(submission=submission).delete()
            freeze_schedule(event.wip_schedule, "3.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    assert {name for name in files if name.startswith("speakers/")} == {
        "speakers/ADA0001.html"
    }
    assert f"speakers/{excluded.code}.html" not in files
    assert f"speakers/{unused.code}.html" not in files


def test_empty_schedule_has_no_speaker_pages(published_schedule, event):
    with scope(event=event):
        SpeakerProfile.objects.create(event=event, name="Unused speaker")
        files = StaticScheduleExporter(event).get_files()

    assert set(files) == {"index.html", "assets/schedule.css"}


@pytest.mark.parametrize("has_biography", (True, False))
def test_speaker_page_biography_uses_rich_text_and_local_links(
    published_talk_slot, published_speaker, event, has_biography
):
    with scope(event=event):
        published_speaker.biography = (
            "Builds **small tools**. [Project](https://example.org). "
            f"[Schedule]({event.urls.schedule.full()}). "
            f"[Session]({published_talk_slot.submission.urls.public.full()})."
            if has_biography
            else None
        )
        published_speaker.internal_notes = "Private speaker notes"
        published_speaker.save(update_fields=["biography", "internal_notes"])
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["speakers/ADA0001.html"], "html.parser")
    biography = page.select_one(".biography")
    assert biography is not None
    if has_biography:
        assert biography.find("strong").get_text() == "small tools"
        assert [
            (link["href"], link.get_text()) for link in biography.find_all("a")
        ] == [
            ("https://example.org", "Project"),
            ("../index.html", "Schedule"),
            ("../sessions/TOOLS01.html", "Session"),
        ]
    else:
        assert biography.get_text() == ""
    assert "Private speaker notes" not in page.get_text()


@pytest.mark.parametrize("request_avatar", (True, False))
@pytest.mark.parametrize("has_photo", (True, False))
def test_speaker_page_uses_the_public_photo(
    image_uploads, published_speaker, event, request_avatar, has_photo
):
    uploads, contents = image_uploads
    with scope(event=event):
        event.cfp.fields["avatar"]["visibility"] = (
            "optional" if request_avatar else "do_not_ask"
        )
        event.cfp.save(update_fields=["fields"])
        if not has_photo:
            published_speaker.refresh_from_db()
            published_speaker.profile_picture = None
            published_speaker.save(update_fields=["profile_picture"])
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["speakers/ADA0001.html"], "html.parser")
    photo = page.select_one(".speaker-avatar")
    path = f"media/{uploads['avatar'].name}"
    if request_avatar and has_photo:
        assert photo is not None
        assert photo["src"] == "../" + quote(path)
        assert photo["alt"] == "Ada Example"
        assert photo.parent.name == "a"
        assert photo.parent["href"] == photo["src"]
        assert files[path] == contents
    else:
        assert photo is None
        assert path not in files


def test_speaker_session_cards_contain_the_public_page_information(
    published_speaker, event, client
):
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    response = client.get(published_speaker.urls.public)
    assert response.status_code == 200
    assert response.wsgi_request.user.is_anonymous
    online = BeautifulSoup(response.content, "html.parser").select(
        ".speaker-talks .pretalx-session"
    )
    page = BeautifulSoup(files["speakers/ADA0001.html"], "html.parser")
    exported = page.select(".speaker-sessions .session")
    assert len(online) == len(exported) == 1
    for selector in (".title", ".speakers", ".abstract", ".track", ".room"):
        value = " ".join(
            online[0].select_one(selector).get_text(" ", strip=True).split()
        )
        assert value in " ".join(exported[0].get_text(" ", strip=True).split())
    assert exported[0].find("a")["href"] == "../sessions/TOOLS01.html"
    assert exported[0].find_all("time")[-1].get_text() == "10:00"
    assert "45 min" in exported[0].get_text(" ", strip=True)
    assert not page.find(["script", "form", "button", "iframe"])

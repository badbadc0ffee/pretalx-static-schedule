import datetime as dt
import posixpath
from io import BytesIO
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signing import Signer
from django.test import Client
from django.utils.translation import override
from django_scopes import scope

from pretalx.agenda.recording import BaseRecordingProvider
from pretalx.agenda.signals import html_below_session_pages, register_recording_provider
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.models import Room, TalkSlot
from pretalx.submission.models import Answer, AnswerOption, Question, Resource

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()).replace(" .", ".")


@pytest.mark.parametrize("language", ("en", "de"))
def test_export_is_english_regardless_of_the_online_page_language(
    published_talk_slot, event, client, language
):
    with scope(event=event):
        event.locales = ["en", "de"]
        event.name = {"en": "Fancy testevent", "de": "Testkonferenz"}
        event.save(update_fields=["locales", "name"])

    response = client.get(
        published_talk_slot.submission.urls.public, {"lang": language}
    )
    assert response.status_code == 200
    online = BeautifulSoup(response.content, "html.parser")
    assert online.html["lang"] == language
    with override(language), scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    for name in ("index.html", "sessions/TOOLS01.html", "speakers/ADA0001.html"):
        page = BeautifulSoup(files[name].decode("utf-8"), "html.parser")
        assert page.html["lang"] == "en"
        assert "Fancy testevent" in page.title.get_text()
        assert "Fancy testevent" not in page.header.get_text()


@pytest.mark.parametrize("deployment", ("/", "/conference/2026/"))
def test_extracted_site_contains_the_public_page_information(
    image_uploads,
    event,
    orga_client,
    register_signal_handler,
    tmp_path,
    deployment,
    day_files,
):
    uploads, image_data = image_uploads
    with scope(event=event):
        submission = event.submissions.get(code="TOOLS01")
        speaker = submission.sorted_speakers.first()
        slot = event.current_schedule.talks.get(submission=submission)
        event.content_locales = ["en", "de"]
        event.feature_flags["attendee_signup"] = True
        event.display_settings["texts"]["agenda_session_above"] = (
            "**Public session notice.**"
        )
        event.save(
            update_fields=["content_locales", "feature_flags", "display_settings"]
        )
        submission.do_not_record = True
        submission.attendee_signup_required = True
        submission.save(update_fields=["do_not_record", "attendee_signup_required"])
        for target, obj in (("submission", submission), ("speaker", speaker)):
            for label, variant, value, public in (
                (f"{target} experience", "text", "Builds **small tools**.", True),
                (f"{target} website", "url", f"https://example.org/{target}", True),
                (f"{target} format", "choices", "", True),
                (f"{target} private", "text", "Private answer", False),
            ):
                question = Question.objects.create(
                    event=event,
                    target=target,
                    question=label,
                    variant=variant,
                    is_public=public,
                )
                answer = Answer.objects.create(
                    question=question, answer=value, **{target: obj}
                )
                if variant == "choices":
                    answer.options.add(
                        AnswerOption.objects.create(
                            question=question, answer="Workshop"
                        )
                    )
        document_question = Question.objects.create(
            event=event,
            question="Public document",
            target="submission",
            variant="file",
            is_public=True,
        )
        document = Answer.objects.create(
            submission=submission,
            question=document_question,
            answer_file=SimpleUploadedFile("document.txt", b"Public document"),
        )
        speaker_document_question = Question.objects.create(
            event=event,
            question="Public speaker document",
            target="speaker",
            variant="file",
            is_public=True,
        )
        speaker_document = Answer.objects.create(
            speaker=speaker,
            question=speaker_document_question,
            answer_file=SimpleUploadedFile("speaker.txt", b"Public speaker document"),
        )
        handout = Resource.objects.create(
            submission=submission,
            description="Public handout",
            is_public=True,
            resource=SimpleUploadedFile("handout.txt", b"Public handout"),
        )
        recording_name = default_storage.save(
            "recordings/session.mp4",
            SimpleUploadedFile("session.mp4", b"Recorded session"),
        )
        recording_url = urljoin(
            submission.urls.public.full(), default_storage.url(recording_name)
        )
        other_room = Room.objects.create(event=event, name="Room B")
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=submission,
            room=other_room,
            start=slot.start + dt.timedelta(days=1),
            end=slot.end + dt.timedelta(days=1),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        event.wip_schedule.talks.update(
            room=None, start=slot.start + dt.timedelta(hours=3)
        )

    class Provider(BaseRecordingProvider):
        def get_recording(self, submission):
            return {
                "iframe": f'<iframe src="{recording_url}" title="Session recording"></iframe>',
                "csp_header": "",
            }

    def recording_handler(signal, sender, **kwargs):
        return Provider(sender)

    def content_handler(signal, sender, **kwargs):
        return '<p id="plugin-notice">Extra equipment is provided.</p>'

    register_signal_handler(register_recording_provider, recording_handler)
    register_signal_handler(html_below_session_pages, content_handler)

    public_client = Client()
    pages = []
    for url in (event.urls.schedule_nojs, submission.urls.public, speaker.urls.public):
        response = public_client.get(url, HTTP_ACCEPT="text/html")
        assert response.status_code == 200
        assert response.wsgi_request.user.is_anonymous
        pages.append(BeautifulSoup(response.content, "html.parser"))
    overview, online_session, online_speaker = pages
    response = orga_client.get(StaticScheduleExporter(event).urls.base)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        archive.extractall(tmp_path)
        names = set(archive.namelist())
        assert {name for name in names if name.endswith(".html")} == {
            "index.html",
            "speakers/index.html",
            *day_files,
            f"tracks/{submission.track_id}.html",
            f"rooms/{slot.room_id}.html",
            f"rooms/{other_room.pk}.html",
            "sessions/TOOLS01.html",
            "speakers/ADA0001.html",
        }
        assert not any(name.endswith(".js") for name in names)
        assert f"media/{recording_name}" not in names
        assert archive.read(f"media/{document.answer_file.name}") == b"Public document"
        assert (
            archive.read(f"media/{speaker_document.answer_file.name}")
            == b"Public speaker document"
        )
        assert archive.read(f"media/{handout.resource.name}") == b"Public handout"
        for key, upload in uploads.items():
            if key == "logo":
                assert f"media/{upload.name}" not in names
                continue
            assert archive.read(f"media/{upload.name}") == image_data

    schedule = BeautifulSoup((tmp_path / "index.html").read_text(), "html.parser")
    session = BeautifulSoup(
        (tmp_path / "sessions/TOOLS01.html").read_text(), "html.parser"
    )
    speaker_page = BeautifulSoup(
        (tmp_path / "speakers/ADA0001.html").read_text(), "html.parser"
    )
    speaker_link = session.select_one('article[data-kind="speaker"] > h2 > a')
    assert speaker_link["href"] == "../speakers/ADA0001.html"
    assert text(speaker_link) == text(speaker_page.h1) == speaker.get_display_name()
    online_slots = overview.select(".pretalx-list-day .pretalx-session")
    exported_slots = schedule.select(
        'article[data-kind="session"][data-view="summary"]'
    )
    assert len(online_slots) == len(exported_slots) == 2
    for online, exported in zip(online_slots, exported_slots, strict=True):
        for selector in (".title", ".speakers", ".abstract", ".track", ".room"):
            assert text(online.select_one(selector)) in text(exported)
        assert text(online.select_one(".start")) == "10:00"
        assert (
            exported.select_one('[data-field="start"] time')
            .get_text()
            .endswith("10:00")
        )
        assert text(online.select_one(".duration")) == "45min"
        assert exported.select_one('[data-field="duration"] data')["value"] == "PT45M"
    online_speaker_slots = online_speaker.select(".speaker-talks .pretalx-session")
    exported_speaker_slots = speaker_page.select(
        '[data-rel="sessions"] article[data-kind="session"]'
    )
    assert len(online_speaker_slots) == len(exported_speaker_slots) == 2
    for online, exported, date in zip(
        online_speaker_slots,
        exported_speaker_slots,
        (slot.start.date(), slot.start.date() + dt.timedelta(days=1)),
        strict=True,
    ):
        for selector in (".title", ".abstract", ".track", ".room"):
            assert text(online.select_one(selector)) in text(exported)
        assert exported.find("a")["href"] == "../sessions/TOOLS01.html"
        assert exported["data-day"] == date.isoformat()
        assert (
            text(online.select(".start")[-1])
            == exported.select_one('[data-field="start"] time')
            .get_text()
            .rsplit(", ", 1)[-1]
        )
        assert text(online.select_one(".duration")) == "45min"
        assert exported.select_one('[data-field="duration"] data')["value"] == "PT45M"
        assert (
            exported.select_one('[data-state="recording-forbidden"]')["aria-label"]
            == online.select_one(".do-not-record-stack")["title"]
        )
    for selector in (".abstract", ".description"):
        assert text(online_session.select_one(f".talk-content {selector}")) == text(
            session.select_one(f'[data-field="{selector[1:]}"]')
        )
    for page in (session, speaker_page):
        assert text(online_speaker.select_one(".speaker-bio")) == text(
            page.select_one('article[data-kind="speaker"] [data-field="biography"]')
        )
        photo = page.select_one(
            'article[data-kind="speaker"] > [data-field="image"] img'
        )
        assert photo["src"] == "../" + quote(f"media/{uploads['avatar'].name}")
        assert photo.parent["href"] == photo["src"]
        assert (tmp_path / "media" / uploads["avatar"].name).read_bytes() == image_data
        for label in ("speaker experience", "speaker format", "Workshop"):
            assert label in text(online_speaker)
            assert label in text(page)
        experience = page.find("dt", string="speaker experience").find_next_sibling(
            "dd"
        )
        assert experience.find("p").get_text() == "Builds small tools."
        assert experience.find("strong").get_text() == "small tools"
        download = page.find(
            "a", href="../" + quote(f"media/{speaker_document.answer_file.name}")
        )
        assert bool(download) == (page is speaker_page)
        assert ("Public speaker document" in text(page)) == (page is speaker_page)
    assert (
        online_speaker.select_one(".speaker-avatar a")["href"] == uploads["avatar"].url
    )
    assert online_speaker.find("a", href=speaker_document.answer_file.url)
    assert (
        tmp_path / "media" / speaker_document.answer_file.name
    ).read_bytes() == b"Public speaker document"
    assert session.select_one('[data-state="signup-open"]')["aria-label"] == text(
        online_session.select_one(".signup-status")
    )
    assert "Language:" in text(online_session)
    assert "English" in text(session)
    assert text(online_session.find(id="plugin-notice")) == text(
        session.find(id="TOOLS01-below-1")
    )
    for label in (
        "Public session notice.",
        "Public document",
        "Public handout",
        "submission experience",
        "submission format",
        "speaker experience",
        "speaker format",
        "Workshop",
    ):
        assert label in text(online_session) + " " + text(online_speaker)
        assert label in text(session)
    assert (
        session.select_one('[data-state="recording-forbidden"]')["aria-label"]
        == online_session.select_one(".do-not-record")["title"]
    )
    for online, exported in zip(online_slots, exported_slots, strict=True):
        assert (
            exported.select_one('[data-state="recording-forbidden"]')["aria-label"]
            == online.select_one(".do-not-record-stack")["title"]
        )
    for target, page in (("submission", online_session), ("speaker", online_speaker)):
        link = page.find(
            "a",
            string=lambda value, target=target: (
                value and value.strip() == f"{target} website"
            ),
        )
        url = Signer(salt="safe-redirect").unsign(
            parse_qs(urlsplit(link["href"]).query)["url"][0]
        )
        assert session.find("a", href=url)
        if target == "speaker":
            assert speaker_page.find("a", href=url)
    assert online_session.find("iframe")["src"] == recording_url
    assert session.select_one('[data-field="recording"] a')["href"] == recording_url
    for name in sorted(name for name in names if name.endswith(".html")):
        page = BeautifulSoup((tmp_path / name).read_text(), "html.parser")
        assert not page.find(["script", "iframe", "form", "button", "video", "audio"])
        assert "Private organiser notes" not in text(page)
        assert "Private answer" not in text(page)
        for element in page.select("[href], [src]"):
            reference = element.get("href", element.get("src"))
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc:
                assert (
                    reference.startswith("https://example.org")
                    or reference == recording_url
                    or reference == event.urls.schedule.full()
                )
                continue
            assert not parsed.path.startswith("/")
            resolved = urlsplit(
                urljoin(f"https://static.example.org{deployment}{name}", reference)
            )
            assert resolved.path.startswith(deployment)
            target = posixpath.normpath(unquote(resolved.path.removeprefix(deployment)))
            assert target in names
            assert (tmp_path / target).is_file()
            if resolved.fragment:
                target_page = BeautifulSoup(
                    (tmp_path / target).read_text(), "html.parser"
                )
                assert target_page.find(id=unquote(resolved.fragment))

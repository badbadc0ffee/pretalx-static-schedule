import datetime as dt
import posixpath
from io import BytesIO
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
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
    return " ".join(node.get_text(" ", strip=True).split())


@pytest.mark.parametrize("language", ("en", "de"))
def test_export_language_matches_the_online_page(
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

    for name in ("index.html", "sessions/TOOLS01.html"):
        page = BeautifulSoup(files[name].decode("utf-8"), "html.parser")
        assert page.html["lang"] == online.html["lang"]
        assert online.header.h1.get_text(strip=True) in page.header.get_text()


@pytest.mark.parametrize("deployment", ("/", "/conference/2026/"))
def test_extracted_site_contains_the_public_page_information(
    image_uploads, event, orga_client, register_signal_handler, tmp_path, deployment
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
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=submission,
            room=Room.objects.create(event=event, name="Room B"),
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
        assert f"media/{recording_name}" not in names
        assert archive.read(f"media/{document.answer_file.name}") == b"Public document"
        assert archive.read(f"media/{handout.resource.name}") == b"Public handout"
        for upload in uploads.values():
            assert archive.read(f"media/{upload.name}") == image_data

    schedule = BeautifulSoup((tmp_path / "index.html").read_text(), "html.parser")
    session = BeautifulSoup(
        (tmp_path / "sessions/TOOLS01.html").read_text(), "html.parser"
    )
    online_slots = overview.select(".pretalx-list-day .pretalx-session")
    exported_slots = schedule.select("article.session")
    assert len(online_slots) == len(exported_slots) == 2
    for online, exported in zip(online_slots, exported_slots, strict=True):
        for selector in (".title", ".speakers", ".abstract", ".track", ".room"):
            assert text(online.select_one(selector)) in text(exported)
        assert text(online.select_one(".start")) == "10:00"
        assert exported.find("time").get_text() == "10:00"
        assert text(online.select_one(".duration")) == "45min"
        assert "45 min" in text(exported)
    for selector in (".abstract", ".description"):
        assert text(online_session.select_one(f".talk-content {selector}")) == text(
            session.select_one(selector)
        )
    assert text(online_speaker.select_one(".speaker-bio")) == text(
        session.select_one(".speaker .biography")
    )
    assert text(online_session.select_one(".signup-status")) in text(session)
    assert "Language:" in text(online_session)
    assert "English" in text(session)
    assert text(online_session.find(id="plugin-notice")) == text(
        session.find(id="plugin-notice")
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
    assert online_session.select_one(".do-not-record")["title"] in text(session)
    for online, exported in zip(online_slots, exported_slots, strict=True):
        assert online.select_one(".do-not-record-stack")["title"] in text(exported)
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
    assert online_session.find("iframe")["src"] == recording_url
    assert session.select_one(".recording a")["href"] == recording_url
    for name in ("index.html", "sessions/TOOLS01.html"):
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

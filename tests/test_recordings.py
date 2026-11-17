import datetime as dt
import posixpath
from io import BytesIO
from urllib.parse import unquote, urljoin, urlsplit
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django_scopes import scope

from pretalx.agenda.recording import BaseRecordingProvider, get_recording
from pretalx.agenda.signals import register_recording_provider
from pretalx.common.urls import get_base_url
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.models import TalkSlot
from pretalx.submission.models import (
    Answer,
    Question,
    Resource,
    Submission,
    SubmissionStates,
)

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.fixture
def recording_provider(register_signal_handler):
    def register(html):
        seen = []

        class Provider(BaseRecordingProvider):
            def get_recording(self, submission):
                seen.append(submission)
                return {"iframe": html, "csp_header": "https://video.example.org"}

        def handler(signal, sender, **kwargs):
            return Provider(sender)

        register_signal_handler(register_recording_provider, handler)
        return seen

    return register


@pytest.mark.parametrize(
    ("title", "label"), (("Video &amp; audio", "Video & audio"), ("", "Recording"))
)
def test_recording_iframe_becomes_an_external_link(
    published_talk_slot, event, recording_provider, title, label
):
    url = "https://video.example.org/embed/123?language=en&quality=hd"
    seen = recording_provider(
        f'<iframe src="{url}" title="{title}" allowfullscreen></iframe>'
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    recording = page.select_one(".recording")
    assert recording is not None
    assert recording.find("h2").get_text(strip=True) == "Recording"
    assert recording.find("a", href=url).get_text(strip=True) == label
    assert seen == [published_talk_slot.submission]
    assert not page.find(["iframe", "script", "video", "audio"])
    assert not any(name.endswith(".js") for name in files)
    assert not BeautifulSoup(files["index.html"], "html.parser").select_one(
        ".recording"
    )


def test_no_recording_does_not_add_a_section(published_talk_slot, event):
    with scope(event=event):
        assert get_recording(published_talk_slot.submission) == {}
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert page.find("h1").get_text() == "Building <tools>"
    assert not page.select_one(".recording")


def test_recording_media_downloads_and_information(
    published_talk_slot, event, recording_provider
):
    recording_provider(
        "<p>Recorded by <strong>Example crew</strong>. License: CC BY 4.0.</p>"
        '<video src="https://video.example.org/video.webm" title="Session video"></video>'
        '<audio src="https://video.example.org/audio.ogg"></audio>'
        '<video><source src="https://video.example.org/video.mp4" type="video/mp4"></video>'
        '<a href="https://video.example.org/download?format=mp4&amp;quality=hd">'
        "Download <em>video &amp; audio</em></a>"
        '<script src="https://video.example.org/player.js">secretPlayer()</script>'
        "<style>.secret-player { color: red }</style>"
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    section = page.select_one(".recording")
    assert section is not None
    assert section.find("p").get_text(strip=True) == (
        "Recorded by Example crew . License: CC BY 4.0. Download video & audio"
    )
    assert [
        (link["href"], link.get_text(strip=True)) for link in section.find_all("a")
    ] == [
        ("https://video.example.org/video.webm", "Session video"),
        ("https://video.example.org/audio.ogg", "Recording"),
        ("https://video.example.org/video.mp4", "video/mp4"),
        (
            "https://video.example.org/download?format=mp4&quality=hd",
            "Download video & audio",
        ),
    ]
    assert not section.find(["iframe", "script", "style", "video", "audio", "source"])
    assert "secretPlayer" not in str(section)
    assert "secret-player" not in str(section)


@pytest.mark.parametrize(
    ("html", "text"),
    (
        (
            "<p>Recording is licensed under CC BY 4.0.</p>",
            "Recording is licensed under CC BY 4.0.",
        ),
        ("<script>loadVideo()</script><style>.video {display:none}</style>", ""),
        ('<iframe title="Unavailable"></iframe>', ""),
    ),
)
def test_recording_without_urls(
    published_talk_slot, event, recording_provider, html, text
):
    recording_provider(html)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    section = page.select_one(".recording")
    if text:
        assert section.find("p").get_text(strip=True) == text
        assert not section.find("a")
    else:
        assert section is None


@pytest.mark.parametrize("url_form", ("relative", "absolute", "protocol-relative"))
@pytest.mark.parametrize("media_url", ("/media/", "https://cdn.example.org/uploads/"))
@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net"))
def test_local_recording_stays_external_in_any_directory(
    published_talk_slot,
    published_speaker,
    event,
    recording_provider,
    settings,
    tmp_path,
    url_form,
    media_url,
    custom_domain,
):
    settings.MEDIA_URL = media_url
    with scope(event=event):
        event.custom_domain = custom_domain
        event.save(update_fields=["custom_domain"])
        name = default_storage.save(
            "recordings/résumé clip.mp4", ContentFile(b"Recorded session")
        )
        original = default_storage.url(name)
        absolute = urljoin(get_base_url(event), original)
        url = {
            "relative": original,
            "absolute": absolute,
            "protocol-relative": "//"
            + urlsplit(absolute).netloc
            + urlsplit(absolute).path,
        }[url_form]
        recording_provider(
            f'<video src="{url}?download=1#t=30" title="Session recording"></video>'
        )
        _, content_type, data = StaticScheduleExporter(event).render(request=None)

    path = f"media/{name}"
    assert content_type == "application/zip"
    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(tmp_path)
        assert set(archive.namelist()) == {
            "index.html",
            "assets/schedule.css",
            "sessions/TOOLS01.html",
            "speakers/ADA0001.html",
        }
        assert not any(
            part in {".", ".."}
            for name in archive.namelist()
            for part in name.split("/")
        )
        assert not any(name.startswith("/") for name in archive.namelist())
    assert not (tmp_path / path).exists()
    page = BeautifulSoup(
        (tmp_path / "sessions/TOOLS01.html").read_text(), "html.parser"
    )
    section = page.select_one(".recording")
    link = section.find("a")
    expected = (
        urljoin(published_talk_slot.submission.urls.public.full(), url)
        + "?download=1#t=30"
    )
    assert link["href"] == expected
    assert link.get_text(strip=True) == "Session recording"
    assert not page.find(["script", "video", "iframe"])
    for deployment in ("/", "/conference/2026/"):
        for element in page.select("[href], [src]"):
            reference = element.get("href", element.get("src"))
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc:
                assert reference in {"https://example.org", expected}
                continue
            assert not reference.startswith("/")
            resolved = urlsplit(
                urljoin(
                    f"https://static.example.org{deployment}sessions/TOOLS01.html",
                    reference,
                )
            )
            assert resolved.path.startswith(deployment)
            target = posixpath.normpath(unquote(resolved.path.removeprefix(deployment)))
            assert (tmp_path / target).is_file()


def test_recording_url_outside_storage_remains_external(
    published_talk_slot, event, recording_provider
):
    url = urljoin(get_base_url(event), "/recordings/watch/123")
    recording_provider(f'<iframe src="{url}" title="Watch recording"></iframe>')
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert page.select_one(".recording a")["href"] == url
    assert not any(name.startswith("media/") for name in files)


@pytest.mark.parametrize(
    "url",
    (
        "",
        "   ",
        "#player",
        "javascript:loadVideo()",
        "data:video/mp4;base64,AA==",
        "file:///recording.mp4",
    ),
)
def test_recording_does_not_export_player_actions(
    published_talk_slot, event, recording_provider, url
):
    recording_provider(f'<iframe src="{url}"></iframe>')
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert not page.select_one(".recording")
    assert not page.find(["script", "iframe"])


def test_relative_provider_links_use_the_original_page_as_base(
    published_talk_slot, event, recording_provider
):
    recording_provider('<a href="../../../recordings/watch/123">Watch recording</a>')
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert page.select_one(".recording a")["href"] == urljoin(
        published_talk_slot.submission.urls.public.full(),
        "../../../recordings/watch/123",
    )


def test_recording_link_does_not_require_the_file_to_be_available(
    published_talk_slot, published_speaker, event, recording_provider
):
    url = default_storage.url("recordings/unavailable.mp4")
    recording_provider(f'<video src="{url}"></video>')
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert set(files) == {
        "index.html",
        "assets/schedule.css",
        "sessions/TOOLS01.html",
        "speakers/ADA0001.html",
    }
    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert page.select_one(".recording a")["href"] == urljoin(
        published_talk_slot.submission.urls.public.full(), url
    )


def test_organiser_download_includes_only_current_session_recordings(
    published_talk_slot, published_speaker, event, recording_provider, orga_client
):
    seen = recording_provider(
        '<iframe src="https://video.example.org/current"></iframe>'
    )
    with scope(event=event):
        event.wip_schedule.talks.all().delete()
        current = Submission.objects.create(
            event=event,
            code="CURRENT1",
            title="Current session",
            submission_type=published_talk_slot.submission.submission_type,
            state=SubmissionStates.CONFIRMED,
        )
        current.speakers.add(published_talk_slot.submission.sorted_speakers.first())
        for offset in (dt.timedelta(), dt.timedelta(days=1)):
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=current,
                room=published_talk_slot.room,
                start=published_talk_slot.start + offset,
                end=published_talk_slot.end + offset,
            )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        wip = Submission.objects.create(
            event=event,
            code="WIP001",
            title="WIP session",
            submission_type=current.submission_type,
            state=SubmissionStates.CONFIRMED,
        )
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=wip,
            room=published_talk_slot.room,
            start=published_talk_slot.start + dt.timedelta(days=2),
            end=published_talk_slot.end + dt.timedelta(days=2),
        )

    response = orga_client.get(StaticScheduleExporter(event).urls.base)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert [submission.code for submission in seen] == ["CURRENT1"]
    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "index.html",
            "assets/schedule.css",
            "sessions/CURRENT1.html",
            "speakers/ADA0001.html",
        }
        page = BeautifulSoup(archive.read("sessions/CURRENT1.html"), "html.parser")
        assert (
            page.select_one(".recording a")["href"]
            == "https://video.example.org/current"
        )
        assert len(page.select(".session-times > li")) == 2


def test_organiser_download_links_to_recording_without_copying_it(
    published_talk_slot,
    published_speaker,
    event,
    recording_provider,
    orga_client,
    settings,
):
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
    name = default_storage.save(
        "recordings/session.mp4", ContentFile(b"Recorded session")
    )
    recording_provider(f'<a href="{default_storage.url(name)}">Download recording</a>')

    response = orga_client.get(StaticScheduleExporter(event).urls.base)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "index.html",
            "assets/schedule.css",
            "sessions/TOOLS01.html",
            "speakers/ADA0001.html",
        }
        page = BeautifulSoup(archive.read("sessions/TOOLS01.html"), "html.parser")
        assert page.select_one(".recording a")["href"] == urljoin(
            published_talk_slot.submission.urls.public.full(), default_storage.url(name)
        )


@pytest.mark.parametrize("attachment", ("resource", "answer"))
@pytest.mark.parametrize("suffix", ("", "?download=1#t=30"))
def test_recording_attachment_stays_outside_the_archive(
    published_talk_slot, event, recording_provider, attachment, suffix
):
    with scope(event=event):
        submission = published_talk_slot.submission
        upload = SimpleUploadedFile("session.mp4", b"Recorded session", "video/mp4")
        if attachment == "resource":
            recording = Resource.objects.create(
                submission=submission,
                description="Session recording",
                is_public=True,
                resource=upload,
            ).resource
        else:
            question = Question.objects.create(
                event=event,
                question="Session recording",
                target="submission",
                variant="file",
                is_public=True,
            )
            recording = Answer.objects.create(
                submission=submission, question=question, answer_file=upload
            ).answer_file
        handout = Resource.objects.create(
            submission=submission,
            description="Handout",
            is_public=True,
            resource=SimpleUploadedFile("handout.txt", b"Public handout"),
        ).resource
        absolute = urljoin(get_base_url(event), recording.url)
        recording_provider(
            f'<video src="{absolute}{suffix}" title="Session recording"></video>'
        )
        _, content_type, data = StaticScheduleExporter(event).render(request=None)

    assert content_type == "application/zip"
    with ZipFile(BytesIO(data)) as archive:
        assert f"media/{recording.name}" not in archive.namelist()
        assert archive.read(f"media/{handout.name}") == b"Public handout"
        page = BeautifulSoup(archive.read("sessions/TOOLS01.html"), "html.parser")
    assert page.select_one(".recording a")["href"] == absolute + suffix
    selector = ".resources a" if attachment == "resource" else ".public-answers a"
    assert page.select_one(selector)["href"] == absolute

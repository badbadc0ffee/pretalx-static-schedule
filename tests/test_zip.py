from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.core.files.uploadedfile import SimpleUploadedFile
from django_scopes import scope
from freezegun import freeze_time
from pytest_django.asserts import assertInHTML

from pretalx.submission.models import Answer, Question, Resource

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def test_render_returns_a_complete_zip(
    published_talk_slot, published_speaker, event, tmp_path, day_files
):
    with scope(event=event), freeze_time("2026-01-15T12:30:00Z"):
        filename, content_type, data = StaticScheduleExporter(event).render(
            request=None
        )

    assert filename == f"{event.slug}-static-schedule--2026-01-15-12-30.zip"
    assert content_type == "application/zip"
    assert isinstance(data, bytes)
    with ZipFile(BytesIO(data)) as archive:
        assert archive.namelist() == [
            "assets/schedule.css",
            "assets/variables.css",
            "index.html",
            *day_files,
            f"tracks/{published_talk_slot.submission.track_id}.html",
            f"rooms/{published_talk_slot.room_id}.html",
            "sessions/TOOLS01.html",
            "speakers/ADA0001.html",
            "speakers/index.html",
        ]
        assert archive.testzip() is None
        for name in archive.namelist():
            assert not PurePosixPath(name).is_absolute()
            assert ".." not in PurePosixPath(name).parts
        root = tmp_path / "web" / "conference"
        archive.extractall(root)

    assert {path.name for path in root.iterdir()} == {
        "index.html",
        "days",
        "assets",
        "tracks",
        "rooms",
        "sessions",
        "speakers",
    }
    assertInHTML(
        '<a href="sessions/TOOLS01.html">Building &lt;tools&gt;</a>',
        (root / "index.html").read_text(encoding="utf-8"),
    )
    detail = (root / "sessions/TOOLS01.html").read_text(encoding="utf-8")
    assertInHTML("<h1>Building &lt;tools&gt;</h1>", detail)
    assertInHTML(f'<a data-field="event" href="../index.html">{event.name}</a>', detail)
    assert "Room A" in detail
    assert "Ada Example" in detail
    assertInHTML(
        "<h1>Ada Example</h1>",
        (root / "speakers/ADA0001.html").read_text(encoding="utf-8"),
    )
    assert b"body" in (root / "assets/schedule.css").read_bytes()


def test_empty_published_schedule_has_a_valid_zip(published_schedule, event, day_files):
    with scope(event=event):
        _, _, data = StaticScheduleExporter(event).render(request=None)

    with ZipFile(BytesIO(data)) as archive:
        assert set(archive.namelist()) == {
            "index.html",
            "speakers/index.html",
            *day_files,
            "assets/schedule.css",
            "assets/variables.css",
        }
        overview = archive.read("index.html").decode("utf-8")
    page = BeautifulSoup(overview, "html.parser")
    assert page.select_one('[data-field="schedule-version"] dd').get_text() == "1.0"
    assert overview.count("No sessions on") == event.duration


def test_extracted_zip_includes_images_and_public_files(
    image_uploads, published_talk_slot, published_speaker, event, tmp_path, day_files
):
    uploads, image_data = image_uploads
    with scope(event=event):
        submission = event.submissions.get(code="TOOLS01")
        question = Question.objects.create(
            event=event, question="Document", variant="file", is_public=True
        )
        answer = Answer.objects.create(
            submission=submission,
            question=question,
            answer="",
            answer_file=SimpleUploadedFile("document.txt", b"Public document"),
        )
        resource = Resource.objects.create(
            submission=submission,
            description="Handout",
            is_public=True,
            resource=SimpleUploadedFile("handout.bin", b"\x00\xffBinary handout"),
        )
        private = Resource.objects.create(
            submission=submission,
            description="Private handout",
            is_public=False,
            resource=SimpleUploadedFile("private.txt", b"Private handout"),
        )
        _, _, data = StaticScheduleExporter(event).render(request=None)

    expected_uploads = {
        f"media/{upload.name}": image_data for upload in uploads.values()
    }
    expected_uploads[f"media/{answer.answer_file.name}"] = b"Public document"
    expected_uploads[f"media/{resource.resource.name}"] = b"\x00\xffBinary handout"
    with ZipFile(BytesIO(data)) as archive:
        assert set(archive.namelist()) == {
            "index.html",
            "speakers/index.html",
            *day_files,
            f"tracks/{submission.track_id}.html",
            f"rooms/{published_talk_slot.room_id}.html",
            "sessions/TOOLS01.html",
            "speakers/ADA0001.html",
            "assets/schedule.css",
            "assets/variables.css",
            *expected_uploads,
        }
        assert f"media/{private.resource.name}" not in archive.namelist()
        archive.extractall(tmp_path)
    for name, contents in expected_uploads.items():
        assert (tmp_path / name).read_bytes() == contents

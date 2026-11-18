import posixpath
import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django_scopes import scope
from pytest_django.asserts import assertInHTML

from pretalx.submission.models import Answer, Question, Resource, Track

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


class HTMLReferences(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.references = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.references.extend(
            value for name, value in attrs if name in {"href", "src"}
        )


def test_bundled_logo_replaces_the_uploaded_logo_and_banner_stays_local(image_uploads, event):
    uploads, contents = image_uploads
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    logo = Path(finders.find("pretalx_static_schedule/logo.svg"))
    assert files["assets/logo.svg"] == logo.read_bytes()
    assert f"media/{uploads['logo'].name}" not in files
    path = f"media/{uploads['header'].name}"
    assert files[path] == contents
    for page, prefix in (("index.html", ""), ("sessions/TOOLS01.html", "../")):
        html = files[page].decode("utf-8")
        assert prefix + quote(path) in HTMLReferences(html).references
        assert prefix + "assets/logo.svg" in HTMLReferences(html).references


def test_session_and_speaker_images_are_local(image_uploads, event):
    uploads, contents = image_uploads
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    for key in ("session", "avatar"):
        path = f"media/{uploads[key].name}"
        assert files[path] == contents
        assert "../" + quote(path) in HTMLReferences(html).references
    assertInHTML(
        f'<a data-field="image" href="../{quote("media/" + uploads["session"].name)}"><img src="../{quote("media/" + uploads["session"].name)}" alt="This session’s header image"></a>',
        html,
    )


def test_disabled_speaker_pictures_are_not_exported(image_uploads, event):
    uploads, _ = image_uploads
    with scope(event=event):
        event.cfp.fields["avatar"]["visibility"] = "do_not_ask"
        event.cfp.save(update_fields=["fields"])
        files = StaticScheduleExporter(event).get_files()

    assert f"media/{uploads['avatar'].name}" not in files
    assert (
        "../" + quote(f"media/{uploads['avatar'].name}")
        not in HTMLReferences(files["sessions/TOOLS01.html"].decode("utf-8")).references
    )


def test_local_stylesheet(published_talk_slot, event):
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert b"body" in files["assets/schedule.css"]
    assert b"@import" not in files["assets/schedule.css"]
    references = re.findall(r'url\("([^\"]+)"\)', files["assets/schedule.css"].decode())
    fonts = []
    for reference in references:
        parts = urlsplit(reference)
        if parts.scheme == "data":
            assert reference.startswith("data:image/svg+xml,")
            continue
        assert not parts.scheme
        assert not parts.netloc
        destination = posixpath.normpath(posixpath.join("assets", reference))
        assert files[destination].startswith(b"wOF2")
        fonts.append(destination)
    assert len(fonts) == 6
    for page, href in (
        ("index.html", "assets/schedule.css"),
        ("sessions/TOOLS01.html", "../assets/schedule.css"),
    ):
        assertInHTML(
            f'<link rel="stylesheet" href="{href}">', files[page].decode("utf-8")
        )


def test_bundled_fonts_and_licenses_are_exported_and_honor_static_overrides(
    published_talk_slot, event, stylesheet_files, settings, tmp_path
):
    static_root = tmp_path / "static"
    custom_font = static_root / "pretalx_static_schedule/fonts/Hack-Regular.woff2"
    custom_font.parent.mkdir(parents=True)
    custom_font.write_bytes(b"Custom font override")
    settings.STATICFILES_DIRS = [static_root, *settings.STATICFILES_DIRS]
    with scope(event=event):
        _, _, data = StaticScheduleExporter(event).render(request=None)
    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(tmp_path / "site")
    font_files = [name for name in stylesheet_files if name.startswith("assets/fonts/")]
    assert len(font_files) == 8
    for name in font_files:
        original = Path(
            finders.find("pretalx_static_schedule/" + name.removeprefix("assets/"))
        )
        assert (tmp_path / "site" / name).read_bytes() == original.read_bytes()
    assert (
        tmp_path / "site/assets/fonts/Hack-Regular.woff2"
    ).read_bytes() == b"Custom font override"


def test_public_downloads_are_local(published_talk_slot, event):
    with scope(event=event):
        submission = published_talk_slot.submission
        document = Question.objects.create(
            event=event, question="Document", variant="file", is_public=True
        )
        answer = Answer.objects.create(
            question=document,
            submission=submission,
            answer="",
            answer_file=SimpleUploadedFile("document notes.txt", b"Public document"),
        )
        handout = Resource.objects.create(
            submission=submission,
            description="Handout",
            resource=SimpleUploadedFile("résumé.txt", b"Public handout"),
            is_public=True,
        )
        private_resource = Resource.objects.create(
            submission=submission,
            description="Private handout",
            resource=SimpleUploadedFile("private.txt", b"Private handout"),
            is_public=False,
        )
        hidden = []
        for public in (False, True):
            question = Question.objects.create(
                event=event,
                question="Hidden document",
                variant="file",
                is_public=public,
            )
            if public:
                question.tracks.add(
                    Track.objects.create(event=event, name="Other track")
                )
            hidden.append(
                Answer.objects.create(
                    question=question,
                    submission=submission,
                    answer="",
                    answer_file=SimpleUploadedFile("hidden.txt", b"Hidden answer"),
                ).answer_file
            )
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    for upload, contents, label in (
        (answer.answer_file, b"Public document", str(answer.answer_file)),
        (handout.resource, b"Public handout", "Handout"),
    ):
        path = f"media/{upload.name}"
        assert files[path] == contents
        assertInHTML(f'<a href="../{quote(path)}">{label}</a>', html)
    for upload in [private_resource.resource, *hidden]:
        assert f"media/{upload.name}" not in files
        assert quote(upload.name) not in html


@pytest.mark.parametrize("absolute", (False, True))
@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net"))
def test_content_links_to_exported_pages_are_relative(
    published_talk_slot, event, absolute, custom_domain
):
    with scope(event=event):
        event.custom_domain = custom_domain
        event.save(update_fields=["custom_domain"])
        submission = published_talk_slot.submission
        talk_url = str(
            submission.urls.public.full() if absolute else submission.urls.public
        )
        overview_url = str(
            event.urls.schedule.full() if absolute else event.urls.schedule
        )
        nojs_url = str(
            event.urls.schedule_nojs.full() if absolute else event.urls.schedule_nojs
        )
        submission.abstract = f"[This session]({talk_url})"
        submission.description = (
            f'[Overview]({overview_url})\n\n`<a href="{talk_url}">Example</a>`'
        )
        submission.save(update_fields=["abstract", "description"])
        speaker = submission.sorted_speakers.first()
        speaker.biography = f"[Plain schedule]({nojs_url})"
        speaker.save(update_fields=["biography"])
        question = Question.objects.create(
            event=event, question="Session URL", variant="text", is_public=True
        )
        Answer.objects.create(
            submission=submission,
            question=question,
            answer=f"[Session details]({talk_url})",
        )
        Resource.objects.create(
            submission=submission,
            link=talk_url,
            description="Related session",
            is_public=True,
        )
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    refs = HTMLReferences(html).references
    assert "TOOLS01.html" in refs
    assert refs.count("../index.html") == 4
    assert talk_url not in refs
    assert refs.count(event.urls.schedule.full()) == 1
    assert (
        overview_url
        not in HTMLReferences(str(BeautifulSoup(html, "html.parser").main)).references
    )
    assert nojs_url not in refs
    assert f'href="{talk_url}"' in html
    for label in ("This session", "Session details", "Related session"):
        assert f">{label}</a>" in html


@pytest.mark.parametrize("absolute", (False, True))
def test_content_links_to_copied_uploads_are_relative(
    published_talk_slot, event, settings, absolute
):
    with scope(event=event):
        submission = published_talk_slot.submission
        resource = Resource.objects.create(
            submission=submission,
            description="Handout",
            is_public=True,
            resource=SimpleUploadedFile("handout.txt", b"Public handout"),
        )
        url = (
            urljoin(settings.SITE_URL, resource.resource.url)
            if absolute
            else resource.resource.url
        )
        submission.description = f"[Download the handout]({url})"
        submission.save(update_fields=["description"])
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    path = f"media/{resource.resource.name}"
    assert files[path] == b"Public handout"
    assert HTMLReferences(html).references.count("../" + quote(path)) == 2
    assert url not in HTMLReferences(html).references


def test_uploaded_html_is_copied_without_changes(published_talk_slot, event):
    contents = b'<!DOCTYPE html><html><head><meta charset="iso-8859-1"></head><body>Handout: \xe4</body></html>'
    with scope(event=event):
        resource = Resource.objects.create(
            submission=published_talk_slot.submission,
            description="HTML handout",
            is_public=True,
            resource=SimpleUploadedFile("handout.html", contents),
        )
        files = StaticScheduleExporter(event).get_files()

    path = f"media/{resource.resource.name}"
    assert files[path] == contents
    assert (
        "../" + quote(path)
        in HTMLReferences(files["sessions/TOOLS01.html"].decode("utf-8")).references
    )


@pytest.mark.parametrize("deployment", ("/", "/conference/2026/"))
def test_internal_references_work_in_any_directory(
    image_uploads, event, tmp_path, deployment
):
    with scope(event=event):
        question = Question.objects.create(
            event=event, question="Document", variant="file", is_public=True
        )
        Answer.objects.create(
            submission=event.submissions.get(code="TOOLS01"),
            question=question,
            answer="",
            answer_file=SimpleUploadedFile("document.txt", b"Document"),
        )
        Resource.objects.create(
            submission=event.submissions.get(code="TOOLS01"),
            description="Handout",
            resource=SimpleUploadedFile("handout.txt", b"Handout"),
            is_public=True,
        )
        files = StaticScheduleExporter(event).get_files()

    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    for name, content in files.items():
        if not name.endswith(".html"):
            continue
        references = HTMLReferences(content.decode("utf-8")).references
        assert references
        for reference in references:
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc:
                assert reference in {"https://example.org", event.urls.schedule.full()}
                continue
            assert not reference.startswith("/")
            resolved = urlsplit(
                urljoin(f"https://static.example.org{deployment}{name}", reference)
            )
            assert resolved.path.startswith(deployment)
            relative = unquote(resolved.path.removeprefix(deployment))
            assert posixpath.normpath(relative) in files
            assert (tmp_path / relative).is_file()

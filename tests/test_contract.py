import datetime as dt
import posixpath
import re
from io import BytesIO
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from bs4 import BeautifulSoup
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.translation import get_language, override
from django_scopes import scope
from PIL import Image, ImageDraw

from pretalx.agenda.recording import BaseRecordingProvider
from pretalx.agenda.signals import (
    html_above_session_pages,
    html_below_session_pages,
    register_recording_provider,
)
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
from pretalx_static_schedule.exporter import StaticScheduleExporter
from pretalx_static_schedule.templatetags.static_schedule import schedule_text

pytestmark = pytest.mark.django_db


@pytest.fixture
def review_export(
    image_uploads,
    published_talk_slot,
    published_speaker,
    event,
    register_signal_handler,
):
    with scope(event=event):
        event.name = {"en": "Template Review Conference", "de": "Vorlagenkonferenz"}
        event.primary_color = "#225588"
        event.date_to = event.date_from + dt.timedelta(days=2)
        event.display_settings["texts"] = {
            "agenda_session_above": "# Welcome\n\nPublic information before the session.",
            "agenda_session_below": "# Further reading\n\nPublic information after the session.",
        }
        event.save()
        event.logo.save(
            "review-logo.svg",
            ContentFile(
                '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="60">'
                '<rect width="180" height="60" rx="8" fill="#225588"/>'
                '<text x="15" y="38" fill="white" font-size="24">Example event</text></svg>'
            ),
        )
        event.header_image.save(
            "review-banner.svg",
            ContentFile(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="200">'
                '<rect width="1200" height="200" fill="#e8f0f8"/>'
                '<circle cx="1050" cy="100" r="130" fill="#ddaa44"/>'
                '<text x="40" y="115" fill="#225588" font-size="42">Ideas, tools and community</text></svg>'
            ),
        )
        submission = published_talk_slot.submission
        submission.image.save(
            "review-session.svg",
            ContentFile(
                '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="180">'
                '<rect width="900" height="180" fill="#f0f4f8"/>'
                '<rect x="30" y="30" width="120" height="120" rx="12" fill="#336699"/>'
                '<text x="180" y="100" fill="#225588" font-size="32">Small tools, practical examples</text></svg>'
            ),
        )
        submission.title = "Kleine Werkzeuge bauen"
        submission.content_locale = "de"
        submission.abstract = "# Motivation\n\nEin **deutscher** Abstract.\n\n### Anwendung\n\nPraktische Beispiele."
        submission.description = (
            "# Grundlagen\n\nEin längerer Beschreibungstext.\n\n"
            '### Code\n\n```html\n<div style="color: red">Example</div>\n```\n\n'
            "## Abschluss\n\n| Feld | Wert |\n| --- | --- |\n| Daten | 42 |"
        )
        submission.track.color = "#336699"
        submission.track.description = "# Tools\n\nPractical ideas and demonstrations."
        submission.track.save()
        submission.save()
        published_speaker.biography = "# Background\n\nBuilds **small tools**.\n\n#### Projects\n\nShares practical examples."
        published_speaker.save()
        portrait = Image.new("RGB", (128, 128), "#e8f0f8")
        drawing = ImageDraw.Draw(portrait)
        drawing.ellipse((44, 20, 84, 60), fill="#225588")
        drawing.ellipse((24, 68, 104, 148), fill="#225588")
        image_data = BytesIO()
        portrait.save(image_data, format="PNG")
        published_speaker.profile_picture.avatar.save(
            "review-avatar.png", ContentFile(image_data.getvalue())
        )
        bob = SpeakerProfile.objects.create(
            event=event,
            code="BOB0001",
            name="bob Example",
            biography="One distinct talk, presented twice.",
        )
        clara = SpeakerProfile.objects.create(
            event=event,
            code="CLARA01",
            name="Clara Example",
            biography="A speaker with one presentation.",
        )
        submission.speakers.add(bob)
        community = Track.objects.create(event=event, name="Community", color="#ddaa44")
        empty_track = Track.objects.create(
            event=event, name="Empty track", color="#8899aa"
        )
        workshop = Room.objects.create(
            event=event,
            name="Workshop Room",
            description="# Directions\n\nThe room is on the ground floor.",
        )
        empty_room = Room.objects.create(event=event, name="Empty room")
        other = Submission.objects.create(
            event=event,
            code="OTHER01",
            title="Testing tools",
            abstract="A second presentation by Ada.",
            submission_type=submission.submission_type,
            state=SubmissionStates.CONFIRMED,
            track=community,
        )
        other.speakers.add(published_speaker)
        solo = Submission.objects.create(
            event=event,
            code="SOLO001",
            title="A single presentation",
            abstract="An untracked session with one speaker.",
            submission_type=submission.submission_type,
            state=SubmissionStates.CONFIRMED,
            do_not_record=True,
        )
        solo.speakers.add(clara)
        night = dt.datetime.combine(
            event.date_from + dt.timedelta(days=1), dt.time(2), tzinfo=event.tz
        )
        for item, room, start in (
            (submission, workshop, night),
            (
                other,
                published_talk_slot.room,
                published_talk_slot.start + dt.timedelta(days=1),
            ),
            (solo, workshop, published_talk_slot.start + dt.timedelta(hours=1)),
        ):
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=item,
                room=room,
                start=start,
                end=start + dt.timedelta(minutes=20),
            )
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            room=published_talk_slot.room,
            slot_type=SlotType.BREAK,
            description="Tea break",
            start=published_talk_slot.end,
            end=published_talk_slot.end + dt.timedelta(minutes=15),
        )
        submission.tags.add(
            Tag.objects.create(
                event=event, tag="Workshop", color="#ffffcc", is_public=True
            ),
            Tag.objects.create(
                event=event, tag="INTERNAL ONLY", color="#123abc", is_public=False
            ),
        )
        question = Question.objects.create(
            event=event, question="Public information", variant="text", is_public=True
        )
        Answer.objects.create(
            submission=submission,
            question=question,
            answer="# Equipment\n\nBring a laptop.\n\n#### Details\n\nPower is provided.",
        )
        speaker_question = Question.objects.create(
            event=event,
            question="Speaker notes",
            target="speaker",
            variant="text",
            is_public=True,
        )
        Answer.objects.create(
            speaker=published_speaker,
            question=speaker_question,
            answer="# Availability\n\nAvailable after the presentation.",
        )
        speaker_document = Question.objects.create(
            event=event,
            question="Speaker document",
            target="speaker",
            variant="file",
            is_public=True,
        )
        Answer.objects.create(
            speaker=published_speaker,
            question=speaker_document,
            answer="",
            answer_file=SimpleUploadedFile(
                "speaker-document.txt", b"Public speaker file"
            ),
        )
        for label, variant in (
            ("Missing file", "file"),
            ("Unanswered question", "text"),
        ):
            question = Question.objects.create(
                event=event, question=label, variant=variant, is_public=True
            )
            Answer.objects.create(submission=submission, question=question, answer="")
        Resource.objects.create(
            submission=submission,
            description="Public handout",
            is_public=True,
            resource=SimpleUploadedFile(
                "review-handout.txt", b"Public review handout\n"
            ),
        )
        Resource.objects.create(
            submission=submission,
            description="INTERNAL ONLY",
            is_public=False,
            link="https://example.org/private",
        )
        freeze_schedule(event.wip_schedule, "Review 1", notify_speakers=False)

    def content_handler(signal, sender, submission, **kwargs):
        if submission.code != "TOOLS01":
            return ""
        return (
            '<section id="equipment"><h1>Equipment</h1><h5>Instructions</h5>'
            '<p id="equipment">Public plugin information.</p>'
            '<a href="#equipment">Equipment reference</a>'
            '<a href="#unrelated">Unresolved source anchor</a></section>'
        )

    class Provider(BaseRecordingProvider):
        def get_recording(self, submission):
            if submission.code == "TOOLS01":
                return {
                    "iframe": '<iframe src="https://video.example.org/recording" title="Session video"></iframe>'
                }
            return {}

    def recording_handler(signal, sender, **kwargs):
        return Provider(sender)

    register_signal_handler(html_above_session_pages, content_handler)
    register_signal_handler(html_below_session_pages, content_handler)
    register_signal_handler(register_recording_provider, recording_handler)
    with scope(event=event), override("de"):
        _, _, archive = StaticScheduleExporter(event).render(request=None)
        assert get_language() == "de"
    with ZipFile(BytesIO(archive)) as zipped:
        files = {name: zipped.read(name) for name in zipped.namelist()}
    return {
        "event": event,
        "files": files,
        "archive": archive,
        "room": workshop.pk,
        "track": submission.track_id,
        "day": event.date_from.isoformat(),
        "empty_track": empty_track.pk,
        "empty_room": empty_room.pk,
    }


def assert_local_references(files, pages):
    for filename, page in pages.items():
        for element in page.select("[href], [src]"):
            reference = urlsplit(element.get("href", element.get("src")))
            if reference.scheme or reference.netloc:
                continue
            assert not reference.path.startswith("/")
            destination = (
                posixpath.normpath(
                    posixpath.join(posixpath.dirname(filename), unquote(reference.path))
                )
                if reference.path
                else filename
            )
            assert destination in files, (filename, reference.geturl())
            if reference.fragment:
                assert pages[destination].find(id=unquote(reference.fragment)), (
                    filename,
                    reference.geturl(),
                )


def test_representative_export_has_clean_structure_and_complete_local_references(
    review_export, tmp_path
):
    files = review_export["files"]
    pages = {
        name: BeautifulSoup(content, "html.parser")
        for name, content in files.items()
        if name.endswith(".html")
    }
    kinds = set()
    heading_levels = set()
    for filename, page in pages.items():
        assert [child.name for child in page.body.find_all(recursive=False)] == [
            "header",
            "main",
            "footer",
        ]
        assert page.html["lang"] == "en"
        assert not page.select(
            "style, script, iframe, [style], [class], [align], [bgcolor], [width], [height]"
        )
        assert len(page.main.find_all("h1")) == 1
        previous = 0
        for heading in page.main.find_all(re.compile(r"^h[1-6]$")):
            rank = int(heading.name[1])
            assert rank <= previous + 1, (filename, str(heading))
            previous = rank
            heading_levels.add(rank)
        identities = [element["id"] for element in page.select("[id]")]
        assert len(identities) == len(set(identities))
        kind = page.main["data-page"]
        kinds.add(kind)
        for article in page.main.find_all("article"):
            assert len(article.find_parents("article")) <= 1
            if kind not in {"session", "speaker"}:
                assert not article.find("article")
        current = page.header.select('[aria-current="page"]')
        assert len(current) == (0 if kind in {"session", "speaker"} else 1)
        if current:
            assert (
                posixpath.normpath(
                    posixpath.join(posixpath.dirname(filename), current[0]["href"])
                )
                == filename
            )
        assert page.footer.find("data")["value"] == __version__
        assert "INTERNAL ONLY" not in page.get_text()
        assert "Private organiser notes" not in page.get_text()
    assert kinds == {
        "sessions",
        "day",
        "room",
        "track",
        "speaker",
        "session",
        "speakers",
    }
    assert {1, 2, 3, 4, 5} <= heading_levels
    assert_local_references(files, pages)
    for filename in (
        f"tracks/{review_export['empty_track']}.html",
        f"rooms/{review_export['empty_room']}.html",
    ):
        assert pages[filename].select_one('[data-state="no-sessions"]')
    session = pages["sessions/TOOLS01.html"]
    assert (
        session.select_one('[data-field="description"] code').get_text().strip()
        == '<div style="color: red">Example</div>'
    )
    occurrences = session.select('[data-rel="occurrences"] > li')
    assert len(occurrences) == 2
    assert occurrences[-1]["data-day"] == review_export["day"]
    assert occurrences[-1]["data-room"] == str(review_export["room"])
    assert "German" in session.select_one('[data-field="language"]').get_text()
    assert not session.select_one(
        'article[data-speaker="BOB0001"] [data-rel="sessions"]'
    )
    assert (
        len(pages["speakers/BOB0001.html"].select('article[data-kind="session"]')) == 2
    )
    assert not pages["speakers/CLARA01.html"].select('[data-rel="speakers"]')
    assert session.find("a", href="https://video.example.org/recording")
    assert all(
        urlsplit(link["href"]).fragment == "unrelated" and urlsplit(link["href"]).netloc
        for link in session.find_all("a", string="Unresolved source anchor")
    )
    (tmp_path / "review.zip").write_bytes(review_export["archive"])
    with ZipFile(BytesIO(review_export["archive"])) as archive:
        archive.extractall(tmp_path / "default")


def test_summary_media_is_limited_to_avatars_and_downloads_stay_on_details(
    review_export,
):
    pages = {
        name: BeautifulSoup(content, "html.parser")
        for name, content in review_export["files"].items()
        if name.endswith(".html")
    }
    summary_contexts = set()
    for page in pages.values():
        for summary in page.select('article[data-view="summary"]'):
            summary_contexts.add((page.main["data-page"], summary["data-kind"]))
            assert not summary.select(
                '[data-rel="files"], [data-field="recording"], [data-state="no-file"]'
            )
            assert not summary.find("dt", string="Speaker document")
            if summary["data-kind"] == "session":
                assert not summary.find("img")
                assert not summary.select(
                    '[data-field="image"], [data-field="public-answers"]'
                )
            elif summary["data-speaker"] == "ADA0001":
                assert len(summary.find_all("img")) == 1
                assert summary.select_one(':scope > [data-field="image"] img')
                assert summary.find("dt", string="Speaker notes")
    assert summary_contexts == {
        ("sessions", "session"),
        ("day", "session"),
        ("track", "session"),
        ("room", "session"),
        ("speaker", "session"),
        ("session", "speaker"),
        ("speakers", "speaker"),
    }
    session = pages["sessions/TOOLS01.html"].main.article
    body = session.select_one(':scope > [data-field="content"]')
    assert body.select_one(':scope > [data-field="image"] img')
    assert body.select_one(':scope > [data-field="abstract"]')
    assert body.select_one(':scope > [data-field="description"]')
    assert (
        session.select_one(':scope > [data-rel="files"] a').get_text()
        == "Public handout"
    )
    assert session.select_one(
        ':scope > [data-field="public-answers"] [data-state="no-file"]'
    )
    speaker = pages["speakers/ADA0001.html"].main.article
    assert speaker.find("dt", string="Speaker document").find_next_sibling("dd").a


def test_custom_stylesheet_selects_days_tracks_rooms_nesting_and_order(
    review_export, settings, tmp_path
):
    selectors = {
        f'article[data-view="summary"][data-room="{review_export["room"]}"]': "border-inline-end: 0.3rem solid navy;",
        f'article[data-view="summary"][data-track="{review_export["track"]}"]': "border-top: 0.3rem dashed purple;",
        f'main > section[data-day="{review_export["day"]}"]': "border-inline-start: 0.2rem dotted teal;",
        'article[data-kind="session"][data-view="detail"] > [data-rel="speakers"] > li > article': "background-color: aliceblue;",
        'article[data-kind="speaker"][data-view="detail"] > [data-rel="sessions"] > li > article': "background-color: ivory;",
        'article > [data-rel="sessions"] > li:nth-child(2)': "border-inline-end: 0.2rem solid green;",
    }
    pages = [
        BeautifulSoup(content, "html.parser")
        for filename, content in review_export["files"].items()
        if filename.endswith(".html")
    ]
    assert all(any(page.select(selector) for page in pages) for selector in selectors)
    stylesheet = (
        review_export["files"]["assets/schedule.css"]
        + "\n".join(
            f"{selector} {{ {rule} }}" for selector, rule in selectors.items()
        ).encode()
    )
    static_root = tmp_path / "static"
    custom = static_root / "pretalx_static_schedule/schedule.css"
    custom.parent.mkdir(parents=True)
    custom.write_bytes(stylesheet)
    settings.STATICFILES_DIRS = [static_root, *settings.STATICFILES_DIRS]
    with scope(event=review_export["event"]):
        files = StaticScheduleExporter(review_export["event"]).get_files()
    assert files["assets/schedule.css"] == stylesheet
    assert all(
        content == files[name]
        for name, content in review_export["files"].items()
        if name.endswith(".html")
    )
    with ZipFile(
        tmp_path / "custom-review.zip", "w", compression=ZIP_DEFLATED
    ) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
            destination = tmp_path / "custom" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)


@pytest.mark.parametrize("level", (1, 3, 5))
def test_authored_headings_follow_the_parent_without_skipping_ranks(level):
    rendered = BeautifulSoup(
        schedule_text(
            "### First\n\n##### Child\n\n# Second\n\n### Child\n\n###### Grandchild",
            level,
        ),
        "html.parser",
    )
    assert [heading.name for heading in rendered.find_all(re.compile(r"^h[1-6]$"))] == [
        f"h{min(6, level + depth)}" for depth in (1, 2, 1, 2, 3)
    ]

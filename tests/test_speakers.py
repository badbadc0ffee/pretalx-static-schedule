import datetime as dt
import posixpath
from io import BytesIO
from urllib.parse import quote, unquote, urlsplit
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.core.files.uploadedfile import SimpleUploadedFile
from django_scopes import scope
from pytest_django.asserts import assertInHTML

from pretalx.person.models import SpeakerProfile
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.models import Room, TalkSlot
from pretalx.submission.models import Answer, Question, Submission, SubmissionStates

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("repeated", (False, True))
def test_single_talk_is_listed_in_the_speaker_overview_and_omitted_on_the_talk_page(
    published_talk_slot, event, repeated
):
    with scope(event=event):
        submission = published_talk_slot.submission
        speaker = submission.sorted_speakers.first()
        if repeated:
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=submission,
                room=published_talk_slot.room,
                start=published_talk_slot.start + dt.timedelta(days=1),
                end=published_talk_slot.end + dt.timedelta(days=1),
            )
            freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        wip = Submission.objects.create(
            event=event,
            code="WIP001",
            title="WIP appearance",
            submission_type=submission.submission_type,
            state=SubmissionStates.CONFIRMED,
        )
        wip.speakers.add(speaker)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=wip,
            room=published_talk_slot.room,
            start=published_talk_slot.end,
            end=published_talk_slot.end + dt.timedelta(minutes=30),
        )
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"], "html.parser")
    for name in ("sessions/TOOLS01.html", "speakers/index.html"):
        section = BeautifulSoup(files[name], "html.parser").find(
            id=f"speaker-{speaker.code}"
        )
        assert section.find("h2").get_text(strip=True) == "Ada Example"
        assertInHTML("<p>Builds <strong>small tools</strong>.</p>", str(section))
        if name == "sessions/TOOLS01.html":
            assert section.select_one(".speaker-sessions") is None
            assert section.find("h3") is None
        else:
            appearances = section.select_one(".speaker-sessions")
            assert appearances is not None
            assert [
                link["href"] for link in appearances.select("li > a:not(.room)")
            ] == ["../sessions/TOOLS01.html"] * (2 if repeated else 1)
            assert section.h3.get_text() == "Sessions"
        assert "WIP appearance" not in section.get_text()
    assert len(page.select(".session-times > li")) == (2 if repeated else 1)
    assert "sessions/WIP001.html" not in files
    speaker_page = BeautifulSoup(files["speakers/ADA0001.html"], "html.parser")
    cards = speaker_page.select(".speaker-sessions .session")
    assert len(cards) == (2 if repeated else 1)
    assert [card.find("a")["href"] for card in cards] == [
        "../sessions/TOOLS01.html"
    ] * len(cards)
    assert "WIP appearance" not in speaker_page.get_text()


@pytest.mark.parametrize(
    "page", ("sessions/TOOLS01.html", "speakers/ADA0001.html", "speakers/index.html")
)
def test_public_speaker_answers(published_talk_slot, event, page):
    with scope(event=event):
        speaker = published_talk_slot.submission.sorted_speakers.first()
        for label, target, public, variant, value in (
            ("Experience", "speaker", True, "text", "Builds **small tools**."),
            ("Website", "speaker", True, "url", "https://example.org/profile"),
            ("Available", "speaker", True, "boolean", "True"),
            ("Needs equipment", "speaker", True, "boolean", "False"),
            ("Private field", "speaker", False, "text", "Private answer"),
            ("Reviewer field", "reviewer", True, "text", "Reviewer answer"),
        ):
            question = Question.objects.create(
                event=event,
                question=label,
                target=target,
                variant=variant,
                is_public=public,
            )
            Answer.objects.create(speaker=speaker, question=question, answer=value)
        files = StaticScheduleExporter(event).get_files()

    html = files[page].decode("utf-8")
    section = BeautifulSoup(html, "html.parser").select_one(".speaker")
    assertInHTML("<dt>Experience</dt>", str(section))
    assertInHTML("<dd><p>Builds <strong>small tools</strong>.</p></dd>", str(section))
    assert section.find("a", href="https://example.org/profile")
    assertInHTML("<dd><p>Yes</p></dd>", str(section))
    assertInHTML("<dd><p>No</p></dd>", str(section))
    assert "Private field" not in html
    assert "Private answer" not in html
    assert "Reviewer answer" not in html


@pytest.mark.parametrize(
    "page", ("sessions/TOOLS01.html", "speakers/ADA0001.html", "speakers/index.html")
)
def test_speaker_downloads_and_full_size_photo(image_uploads, event, tmp_path, page):
    uploads, image_data = image_uploads
    with scope(event=event):
        speaker = event.submissions.get(code="TOOLS01").sorted_speakers.first()
        answers = []
        for label, public, file in (
            ("Public document", True, SimpleUploadedFile("document.bin", b"Document")),
            ("Missing document", True, None),
            ("Private document", False, SimpleUploadedFile("private.bin", b"Private")),
        ):
            question = Question.objects.create(
                event=event,
                question=label,
                target="speaker",
                variant="file",
                is_public=public,
            )
            answers.append(
                Answer.objects.create(
                    speaker=speaker, question=question, answer="", answer_file=file
                )
            )
        _, _, data = StaticScheduleExporter(event).render(request=None)

    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(tmp_path)
        assert f"media/{answers[2].answer_file.name}" not in archive.namelist()
    section = BeautifulSoup((tmp_path / page).read_text(), "html.parser").select_one(
        ".speaker"
    )
    document = answers[0].answer_file
    assertInHTML(
        f'<a href="../media/{quote(document.name)}">{document}</a>', str(section)
    )
    assert (tmp_path / f"media/{document.name}").read_bytes() == b"Document"
    assert "No file provided" in section.get_text()
    assert "Private document" not in section.get_text()
    image = section.find("img")
    assert (tmp_path / f"media/{uploads['avatar'].name}").read_bytes() == image_data
    assert image.parent.name == "a"
    assert image.parent["href"] == image["src"]


@pytest.mark.parametrize("absolute", (False, True))
@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net"))
def test_speaker_links_resolve_to_exported_pages(
    published_talk_slot, event, absolute, custom_domain
):
    with scope(event=event):
        event.custom_domain = custom_domain
        event.save(update_fields=["custom_domain"])
        submission = published_talk_slot.submission
        ada = submission.sorted_speakers.first()
        bob = SpeakerProfile.objects.create(event=event, name="Bob Example")
        ada_url = ada.urls.public.full() if absolute else ada.urls.public
        bob_url = bob.urls.public.full() if absolute else bob.urls.public
        for speaker, name, url in ((ada, "Bob", bob_url), (bob, "Ada", ada_url)):
            speaker.biography = f"[About {name}]({url})"
            speaker.save(update_fields=["biography"])
            question = Question.objects.create(
                event=event,
                question=f"{name}'s page",
                target="speaker",
                variant="text",
                is_public=True,
            )
            Answer.objects.create(
                speaker=speaker, question=question, answer=f"[{name}'s page]({url})"
            )
        submission.description = f"[Bob's profile]({bob_url})"
        submission.save(update_fields=["description"])
        other = Submission.objects.create(
            event=event,
            code="OTHER01",
            title="Other session",
            submission_type=submission.submission_type,
            state=SubmissionStates.CONFIRMED,
            description=f"[Ada's profile]({ada_url})",
        )
        other.speakers.add(bob)
        start = published_talk_slot.end + dt.timedelta(minutes=15)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=other,
            room=published_talk_slot.room,
            start=start,
            end=start + dt.timedelta(minutes=30),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    pages = {
        name: BeautifulSoup(content.decode("utf-8"), "html.parser")
        for name, content in files.items()
        if name.endswith(".html")
    }
    assert pages["sessions/TOOLS01.html"].find(id=f"speaker-{ada.code}")
    assert pages["sessions/OTHER01.html"].find(id=f"speaker-{bob.code}")
    assert pages["sessions/TOOLS01.html"].find(
        "a", href=f"../speakers/{bob.code}.html", string="Bob's profile"
    )
    assert pages["sessions/OTHER01.html"].find(
        "a", href=f"../speakers/{ada.code}.html", string="Ada's profile"
    )
    for speaker, other, name in ((ada, bob, "Bob"), (bob, ada, "Ada")):
        page = pages[f"speakers/{speaker.code}.html"]
        for label in (f"About {name}", f"{name}'s page"):
            assert page.find("a", href=f"{other.code}.html", string=label)
    for name, page in pages.items():
        for link in page.find_all("a", href=True):
            url = urlsplit(link["href"])
            if url.scheme or url.netloc:
                assert link["href"] in {
                    "https://example.org",
                    event.urls.schedule.full(),
                }
                continue
            assert not url.path.startswith("/")
            target = (
                posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), unquote(url.path))
                )
                if url.path
                else name
            )
            assert target in files
            if url.fragment:
                assert pages[target].find(id=url.fragment)


def test_all_speaker_appearances_use_current_published_slots(
    published_talk_slot, event
):
    with scope(event=event):
        submission = published_talk_slot.submission
        speaker = submission.sorted_speakers.first()
        current_room = Room.objects.create(event=event, name="Current room")
        current_start = published_talk_slot.start + dt.timedelta(minutes=30)
        event.wip_schedule.talks.filter(submission=submission).update(
            room=current_room,
            start=current_start,
            end=current_start + dt.timedelta(minutes=45),
        )
        repeated_start = published_talk_slot.start + dt.timedelta(days=1)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=submission,
            room=Room.objects.create(event=event, name="Repeated room"),
            start=repeated_start,
            end=repeated_start + dt.timedelta(minutes=15),
        )
        other = Submission.objects.create(
            event=event,
            code="OTHER01",
            title="Other appearance",
            submission_type=submission.submission_type,
            state=SubmissionStates.CONFIRMED,
            description=f"[Speaker profile]({speaker.urls.public.full()})",
        )
        other.speakers.add(
            speaker, SpeakerProfile.objects.create(event=event, name="Bob Example")
        )
        other_start = published_talk_slot.start + dt.timedelta(hours=1)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=other,
            room=Room.objects.create(event=event, name="Other room"),
            start=other_start,
            end=other_start + dt.timedelta(minutes=30),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        wip = Submission.objects.create(
            event=event,
            code="WIP001",
            title="WIP appearance",
            submission_type=submission.submission_type,
            state=SubmissionStates.CONFIRMED,
        )
        wip.speakers.add(speaker)
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=wip,
            room=current_room,
            start=other_start + dt.timedelta(days=2),
            end=other_start + dt.timedelta(days=2, minutes=30),
        )
        files = StaticScheduleExporter(event).get_files()

    for name, prefix in (
        ("sessions/TOOLS01.html", ""),
        ("sessions/OTHER01.html", ""),
        ("speakers/index.html", "../sessions/"),
    ):
        page = BeautifulSoup(files[name].decode("utf-8"), "html.parser")
        section = page.find(id=f"speaker-{speaker.code}")
        appearances = section.select_one(".speaker-sessions")
        assert appearances is not None
        assert [time["datetime"] for time in appearances.find_all("time")][::2] == [
            current_start.isoformat(),
            other_start.isoformat(),
            repeated_start.isoformat(),
        ]
        assert [link["href"] for link in appearances.select("li > a:not(.room)")] == [
            prefix + "TOOLS01.html",
            prefix + "OTHER01.html",
            prefix + "TOOLS01.html",
        ]
        text = appearances.get_text(" ", strip=True)
        for value in (
            "Current room",
            "Other room",
            "Repeated room",
            "45 min",
            "30 min",
            "15 min",
        ):
            assert value in text
        assert "Room A" not in text
        assert "WIP appearance" not in text
        assert len(section.select(".speaker-sessions > li")) == 3
    overview = BeautifulSoup(files["speakers/index.html"], "html.parser")
    assert [
        section.h2.get_text(strip=True)
        for section in overview.select(".speaker-list > .speaker")
    ] == ["Ada Example", "Bob Example"]
    bob_appearances = overview.select_one(
        ".speaker-list > .speaker:last-child .speaker-sessions"
    )
    assert bob_appearances is not None
    assert [link["href"] for link in bob_appearances.select("li > a:not(.room)")] == [
        "../sessions/OTHER01.html"
    ]
    assert "sessions/WIP001.html" not in files
    assert BeautifulSoup(
        files["sessions/OTHER01.html"].decode("utf-8"), "html.parser"
    ).find("a", href=f"../speakers/{speaker.code}.html")
    bob = (
        BeautifulSoup(files["sessions/OTHER01.html"], "html.parser")
        .find("a", string="Bob Example")
        .find_parent("section", class_="speaker")
    )
    assert bob.select_one(".speaker-sessions") is None
    assert bob.find("h3") is None
    speaker_page = BeautifulSoup(files["speakers/ADA0001.html"], "html.parser")
    cards = speaker_page.select(".speaker-sessions .session")
    assert len(cards) == 3
    assert [card.find("a")["href"] for card in cards] == [
        "../sessions/TOOLS01.html",
        "../sessions/OTHER01.html",
        "../sessions/TOOLS01.html",
    ]
    for card, start, room, duration in zip(
        cards,
        (current_start, other_start, repeated_start),
        ("Current room", "Other room", "Repeated room"),
        (45, 30, 15),
        strict=True,
    ):
        assert card.find("time", attrs={"datetime": start.date().isoformat()})
        assert card.find("time", attrs={"datetime": start.isoformat()})
        assert room in card.get_text()
        assert f"{duration} min" in card.get_text(" ", strip=True)
    assert all("Room A" not in card.get_text() for card in cards)
    assert "WIP appearance" not in speaker_page.get_text()

import datetime as dt
import posixpath
from copy import deepcopy

import pytest
from bs4 import BeautifulSoup
from django_scopes import scope

from pretalx.person.models import SpeakerProfile
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.models import Room, TalkSlot
from pretalx.submission.models import Submission, SubmissionStates, Tag

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def normalized_summary(article, filename, relationship):
    article = deepcopy(article)
    related = article.find(attrs={"data-rel": relationship}, recursive=False)
    if related:
        related.decompose()
    heading = article.find(["h2", "h3", "h4"], recursive=False)
    heading.name = "h2"
    for link in article.select("a[href]"):
        if not link["href"].startswith("http"):
            link["href"] = posixpath.normpath(
                posixpath.join(posixpath.dirname(filename), link["href"])
            )
    for image in article.select("img[src]"):
        image["src"] = posixpath.normpath(
            posixpath.join(posixpath.dirname(filename), image["src"])
        )
    for whitespace in article.find_all(string=lambda text: not text.strip()):
        if whitespace.parent.name not in {"pre", "code"}:
            whitespace.extract()
    return article


@pytest.mark.parametrize("repeated", (False, True))
def test_root_single_relationships_appear_and_parent_backlinks_are_omitted(
    published_talk_slot, event, repeated
):
    with scope(event=event):
        if repeated:
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=published_talk_slot.submission,
                room=published_talk_slot.room,
                start=published_talk_slot.start + dt.timedelta(days=1),
                end=published_talk_slot.end + dt.timedelta(days=1),
            )
            freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()
    for filename in ("index.html", "sessions/TOOLS01.html"):
        page = BeautifulSoup(files[filename], "html.parser")
        session = page.main.find("article")
        speakers = session.find(attrs={"data-rel": "speakers"}, recursive=False)
        assert len(speakers.find_all("li", recursive=False)) == 1
        if filename == "index.html":
            assert speakers.a["data-speaker"] == "ADA0001"
            assert not speakers.find("article")
        else:
            assert speakers.article["data-speaker"] == "ADA0001"
            assert not speakers.article.select('[data-rel="sessions"]')
    for filename in ("speakers/index.html", "speakers/ADA0001.html"):
        page = BeautifulSoup(files[filename], "html.parser")
        speaker = page.main.find("article")
        presentations = speaker.find(attrs={"data-rel": "sessions"}, recursive=False)
        assert len(presentations.find_all("li", recursive=False)) == (
            2 if repeated else 1
        )
        if filename == "speakers/index.html":
            assert not presentations.find("article")
            assert all(
                item["data-session"] == "TOOLS01"
                for item in presentations.find_all("li", recursive=False)
            )
        else:
            assert all(
                item["data-session"] == "TOOLS01"
                for item in presentations.select("article")
            )
        assert not presentations.select('[data-rel="speakers"]')


@pytest.fixture
def shared_presentations(image_uploads, event):
    with scope(event=event):
        submission = event.submissions.get(code="TOOLS01")
        ada = submission.sorted_speakers.first()
        bob = SpeakerProfile.objects.create(
            event=event, name="Bob Example", code="BOB0001"
        )
        submission.speakers.add(bob)
        submission.content_locale = "de"
        submission.abstract = "Ein **deutscher** Abstract."
        submission.save(update_fields=["content_locale", "abstract"])
        other = Submission.objects.create(
            event=event,
            code="OTHER01",
            title="Second presentation",
            abstract="A second abstract.",
            submission_type=submission.submission_type,
            state=SubmissionStates.CONFIRMED,
            track=submission.track,
        )
        other.speakers.add(ada)
        room = Room.objects.create(event=event, name="Night room")
        night = dt.datetime.combine(
            event.date_from + dt.timedelta(days=1), dt.time(2), tzinfo=event.tz
        )
        for item, start in (
            (submission, night),
            (other, night + dt.timedelta(minutes=30)),
        ):
            TalkSlot.objects.create(
                schedule=event.wip_schedule,
                submission=item,
                room=room,
                start=start,
                end=start + dt.timedelta(minutes=20),
            )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        return StaticScheduleExporter(event).get_files()


def test_shared_presentations_keep_complete_compact_relationships(shared_presentations):
    page = BeautifulSoup(shared_presentations["sessions/TOOLS01.html"], "html.parser")
    ada = page.main.select_one('article[data-speaker="ADA0001"]')
    related = ada.select_one('[data-rel="sessions"]')
    assert [
        item["data-session"] for item in related.find_all("li", recursive=False)
    ] == ["TOOLS01", "TOOLS01", "OTHER01"]
    assert not related.find("article")
    for item in related.find_all("li", recursive=False):
        assert item["data-slot"]
        assert item["data-day"]
        assert item["data-room"]
        assert len(item.find_all("time")) == 2
    assert not page.main.select_one('article[data-speaker="BOB0001"]').select(
        '[data-rel="sessions"]'
    )
    speaker = BeautifulSoup(
        shared_presentations["speakers/ADA0001.html"], "html.parser"
    )
    for session in speaker.main.select('article[data-kind="session"]'):
        related = session.find(attrs={"data-rel": "speakers"}, recursive=False)
        if session["data-session"] == "TOOLS01":
            assert [link["data-speaker"] for link in related.select("a")] == [
                "ADA0001",
                "BOB0001",
            ]
            assert not related.find("article")
        else:
            assert related is None


def test_summaries_share_the_same_fields_across_pages(shared_presentations):
    overview = BeautifulSoup(shared_presentations["index.html"], "html.parser")
    slot = overview.main.select_one('article[data-session="TOOLS01"]')["data-slot"]
    session_summaries = []
    speaker_summaries = []
    for filename, content in shared_presentations.items():
        if not filename.endswith(".html"):
            continue
        page = BeautifulSoup(content, "html.parser")
        session = page.main.select_one(
            f'article[data-view="summary"][data-session="TOOLS01"][data-slot="{slot}"]'
        )
        if session:
            session_summaries.append(normalized_summary(session, filename, "speakers"))
        speaker = page.main.select_one(
            'article[data-view="summary"][data-speaker="ADA0001"]'
        )
        if speaker:
            speaker_summaries.append(normalized_summary(speaker, filename, "sessions"))
    assert len(session_summaries) >= 5
    assert all(item == session_summaries[0] for item in session_summaries)
    assert len(speaker_summaries) >= 3
    assert all(item == speaker_summaries[0] for item in speaker_summaries)


def test_articles_have_bounded_nesting_semantic_hooks_and_content_languages(
    shared_presentations, event
):
    for filename, content in shared_presentations.items():
        if not filename.endswith(".html"):
            continue
        page = BeautifulSoup(content, "html.parser")
        assert len(page.main.find_all("h1")) == 1
        assert not page.select("[style], style, script")
        identities = [element["id"] for element in page.select("[id]")]
        assert len(identities) == len(set(identities))
        for article in page.main.find_all("article"):
            assert len(article.find_parents("article")) <= 1
            heading = article.find(["h1", "h2", "h3", "h4"], recursive=False)
            parent = article.find_parent("article")
            if page.main["data-page"] in {"session", "speaker"}:
                if article["data-view"] == "detail":
                    assert article.find("article")
            else:
                assert not article.find("article")
            if parent:
                parent_heading = parent.find(["h1", "h2", "h3"], recursive=False)
                assert int(heading.name[1]) == int(parent_heading.name[1]) + 1
            if article["data-kind"] == "session":
                assert "data-session" in article.attrs
                if article["data-view"] == "summary":
                    assert {
                        "data-slot",
                        "data-room",
                        "data-track",
                        "data-day",
                    } <= article.attrs.keys()
                if article["data-session"] == "TOOLS01":
                    assert heading["lang"] == "de"
                    assert article.select_one('[data-field="abstract"]')["lang"] == "de"
            else:
                assert "lang" not in article.attrs
                biography = article.find(
                    attrs={"data-field": "biography"}, recursive=False
                )
                if biography:
                    assert "lang" not in biography.attrs
        for relationship in page.main.select("[data-rel]"):
            assert relationship.find("li", recursive=False)
    page = BeautifulSoup(shared_presentations["speakers/ADA0001.html"], "html.parser")
    night = page.main.select('article[data-session="TOOLS01"]')[-1]
    assert night["data-day"] == event.date_from.isoformat()
    assert night.select_one('[data-field="start"] time')["datetime"].startswith(
        (event.date_from + dt.timedelta(days=1)).isoformat()
    )


def test_invalid_color_data_cannot_add_css_rules(published_talk_slot, event):
    malicious = "#123456; } body { display: none; } /*"
    with scope(event=event):
        track = published_talk_slot.submission.track
        track.color = malicious
        track.save(update_fields=["color"])
        tag = Tag.objects.create(
            event=event, tag="Invalid color", color=malicious, is_public=True
        )
        published_talk_slot.submission.tags.add(tag)
        files = StaticScheduleExporter(event).get_files()
    css = files["assets/variables.css"].decode()
    assert malicious not in css
    assert (
        f'[data-track="{track.pk}"] {{\n    --track-color: var(--color-primary);' in css
    )
    assert (
        f'[data-tag="{tag.pk}"] {{\n    background-color: var(--color-primary);' in css
    )

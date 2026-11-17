import datetime as dt
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.templatetags.static import static
from django_scopes import scope

from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.enums import SlotType
from pretalx.schedule.models import TalkSlot
from pretalx.submission.models import Tag

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def test_original_variables_stylesheet_is_exported(
    published_talk_slot, event, client, tmp_path
):
    response = client.get(published_talk_slot.submission.urls.public)
    assert response.status_code == 200
    online = BeautifulSoup(response.content, "html.parser")
    assert online.find("link", href=static("common/css/_variables.css"))
    original = Path(finders.find("common/css/_variables.css")).read_bytes()
    with scope(event=event):
        _, _, data = StaticScheduleExporter(event).render(request=None)

    with ZipFile(BytesIO(data)) as archive:
        assert archive.read("assets/variables.css") == original
        archive.extractall(tmp_path)
    assert (tmp_path / "assets/variables.css").read_bytes() == original
    assert b"--color-primary:" in original
    assert b"@import" not in original
    assert b"url(" not in original
    for name, prefix in (
        ("index.html", ""),
        ("sessions/TOOLS01.html", "../"),
        ("speakers/ADA0001.html", "../"),
    ):
        page = BeautifulSoup((tmp_path / name).read_text(), "html.parser")
        assert [link["href"] for link in page.find_all("link", rel="stylesheet")] == [
            prefix + "assets/variables.css",
            prefix + "assets/schedule.css",
        ]


@pytest.mark.parametrize("color", ("", "#123456", "#ffffcc"))
def test_pages_use_the_event_color(published_talk_slot, event, color, settings, client):
    with scope(event=event):
        event.primary_color = color
        event.save(update_fields=["primary_color"])
        files = StaticScheduleExporter(event).get_files()

    cache.clear()
    response = client.get(event.urls.settings_css)
    assert response.status_code == 200
    original = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", response.content.decode()))
    for name in ("index.html", "sessions/TOOLS01.html", "speakers/ADA0001.html"):
        page = BeautifulSoup(files[name], "html.parser")
        style = page.find("style")
        assert style is not None
        assert ":root" in style.get_text()
        definitions = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", style.get_text()))
        assert definitions["--color-primary"] == (
            color or settings.DEFAULT_EVENT_PRIMARY_COLOR
        )
        for variable, value in original.items():
            assert definitions[variable] == value
        if color == "#ffffcc":
            assert definitions["--color-text-on-primary"] == "var(--color-text)"
        else:
            assert "--color-text-on-primary" not in definitions
    css = files["assets/schedule.css"].decode()
    assert "color: var(--color-text);" in css
    assert "background-color: var(--color-bg);" in css
    assert "color: var(--color-primary-text);" in css
    assert "color: var(--color-primary-text-dark);" in css
    assert "border: 1px solid var(--color-border);" in css


@pytest.mark.parametrize("has_track", (True, False))
@pytest.mark.parametrize("color", ("", "#2a6b8c"))
@pytest.mark.parametrize("use_tracks", (True, False))
def test_session_cards_and_details_use_the_assigned_track_color(
    published_talk_slot, event, has_track, color, use_tracks
):
    with scope(event=event):
        event.feature_flags["use_tracks"] = use_tracks
        event.save(update_fields=["feature_flags"])
        submission = published_talk_slot.submission
        submission.track.color = color
        submission.track.save(update_fields=["color"])
        if not has_track:
            submission.track = None
            submission.save(update_fields=["track"])
        files = StaticScheduleExporter(event).get_files()

    expected = color if has_track and color else "var(--color-primary)"
    for name, selector in (
        ("index.html", ".session"),
        ("sessions/TOOLS01.html", ".session-detail"),
        ("speakers/ADA0001.html", ".session"),
    ):
        page = BeautifulSoup(files[name], "html.parser")
        article = page.select_one(selector)
        assert article is not None
        style = dict(re.findall(r"(--[\w-]+):\s*([^;]+)", article.get("style", "")))
        assert style.get("--track-color", "").strip() == expected
        if has_track:
            assert "Tools" in article.select_one(".track").get_text()
        else:
            assert article.select_one(".track") is None
    css = files["assets/schedule.css"].decode()
    assert "border-inline-start: 0.3rem solid var(--track-color);" in css
    assert ".track {\n    color: var(--track-color);\n}" in css


def test_breaks_use_a_neutral_color(published_talk_slot, event):
    with scope(event=event):
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            room=published_talk_slot.room,
            slot_type=SlotType.BREAK,
            description="Tea break",
            start=published_talk_slot.end,
            end=published_talk_slot.end + dt.timedelta(minutes=15),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["index.html"], "html.parser")
    article = page.find("h3", string="Tea break").find_parent("article")
    assert article["style"] == "--track-color: var(--color-grey-light)"
    assert article.select_one(".track") is None


@pytest.mark.parametrize("has_public_tags", (True, False))
def test_public_tags_use_their_configured_colors(
    published_talk_slot, event, has_public_tags
):
    with scope(event=event):
        tags = [
            Tag.objects.create(event=event, tag=name, color=color, is_public=public)
            for name, color, public in (
                ("Public & <tag>", "#123abc", has_public_tags),
                ("Workshop", "#abc123", has_public_tags),
                ("Private planning", "#ef1234", False),
            )
        ]
        published_talk_slot.submission.tags.add(*tags)
        Tag.objects.create(
            event=event, tag="Unused public tag", color="#abc456", is_public=True
        )
        files = StaticScheduleExporter(event).get_files()

    for name in ("index.html", "sessions/TOOLS01.html", "speakers/ADA0001.html"):
        page = BeautifulSoup(files[name], "html.parser")
        badges = page.select(".tag-badge")
        assert len(badges) == (2 if has_public_tags else 0)
        assert {(badge.get_text(), badge["style"]) for badge in badges} == (
            {
                ("Public & <tag>", "background-color: #123abc"),
                ("Workshop", "background-color: #abc123"),
            }
            if has_public_tags
            else set()
        )
        assert "Private planning" not in page.get_text()
        assert "Unused public tag" not in page.get_text()
        assert "#ef1234" not in files[name].decode()
        if has_public_tags:
            assert "Public &amp; &lt;tag&gt;" in files[name].decode()
        else:
            assert page.select_one(".tags") is None
    css = files["assets/schedule.css"].decode()
    assert ".tag-badge {" in css
    assert "color: var(--color-white);" in css

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


def test_original_variables_stylesheet_is_exported_with_event_overrides(
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
        exported = archive.read("assets/variables.css")
        assert exported.startswith(original)
        assert b":root" in exported[len(original) :]
        archive.extractall(tmp_path)
    assert (tmp_path / "assets/variables.css").read_bytes() == exported
    assert b"--color-primary:" in original
    assert b"@import" not in original
    assert b"url(" not in original
    for name, prefix in (
        ("index.html", ""),
        ("sessions/TOOLS01.html", "../"),
        ("speakers/ADA0001.html", "../"),
        ("speakers/index.html", "../"),
    ):
        page = BeautifulSoup((tmp_path / name).read_text(), "html.parser")
        assert [link["href"] for link in page.find_all("link", rel="stylesheet")] == [
            prefix + "assets/variables.css",
            prefix + "assets/schedule.css",
        ]


@pytest.mark.parametrize(
    ("color", "foreground"),
    (
        ("", "var(--color-text)"),
        ("#123456", "var(--color-white)"),
        ("#ffffcc", "var(--color-text)"),
        ("#fff", "var(--color-text)"),
        ("#123", "var(--color-white)"),
        ("#777777", "var(--color-text)"),
    ),
)
def test_event_color_variables_are_exported(
    published_talk_slot, event, color, foreground, settings, client
):
    with scope(event=event):
        event.primary_color = color
        event.save(update_fields=["primary_color"])
        files = StaticScheduleExporter(event).get_files()

    cache.clear()
    response = client.get(event.urls.settings_css)
    assert response.status_code == 200
    original = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", response.content.decode()))
    base = Path(finders.find("common/css/_variables.css")).read_bytes()
    overrides = files["assets/variables.css"][len(base) :].decode()
    assert ":root" in overrides
    definitions = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", overrides))
    assert definitions["--color-primary"] == (
        color or settings.DEFAULT_EVENT_PRIMARY_COLOR
    )
    assert definitions["--color-primary"] == original.get(
        "--color-primary", settings.DEFAULT_EVENT_PRIMARY_COLOR
    )
    assert definitions["--color-text-on-primary"] == foreground
    for name in (
        "index.html",
        "sessions/TOOLS01.html",
        "speakers/ADA0001.html",
        "speakers/index.html",
    ):
        page = BeautifulSoup(files[name], "html.parser")
        assert not page.find("style")
    css = files["assets/schedule.css"].decode()
    assert "color: var(--color-text);" in css
    assert "background-color: var(--color-bg);" in css
    assert "color: var(--color-primary-text);" in css
    assert "color: var(--color-primary-text-dark);" in css
    assert "border: 1px solid var(--color-border);" in css


@pytest.mark.parametrize("has_track", (True, False))
@pytest.mark.parametrize("color", ("", "#2a6b8c", "#aBc"))
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
    variables = files["assets/variables.css"].decode()
    for name, selector in (
        ("index.html", 'article:is([data-kind="session"], [data-kind="break"])'),
        ("sessions/TOOLS01.html", 'article[data-kind="session"][data-view="detail"]'),
        (
            "speakers/ADA0001.html",
            'article:is([data-kind="session"], [data-kind="break"])',
        ),
    ):
        page = BeautifulSoup(files[name], "html.parser")
        article = page.select_one(selector)
        assert article is not None
        assert "style" not in article.attrs
        if has_track:
            assert article["data-track"] == str(submission.track_id)
            assert (
                f'[data-track="{submission.track_id}"] {{\n    --track-color: {expected};'
                in variables
            )
            assert "Tools" in article.select_one('[data-field="track"]').get_text()
        else:
            assert "data-track" not in article.attrs
            assert article.select_one('[data-field="track"]') is None
    css = files["assets/schedule.css"].decode()
    assert "border-inline-start: 0.3rem solid var(--track-color);" in css
    assert "text-decoration-color: var(--track-color);" in css


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
    assert "style" not in article.attrs
    assert article["data-kind"] == "break"
    assert (
        'article[data-kind="break"] {\n    --track-color: var(--color-grey-light);'
        in files["assets/schedule.css"].decode()
    )
    assert article.select_one('[data-field="track"]') is None


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
        badges = page.select("[data-tag]")
        assert len(badges) == (2 if has_public_tags else 0)
        assert {(badge.get_text(), badge["data-tag"]) for badge in badges} == (
            {("Public & <tag>", str(tags[0].pk)), ("Workshop", str(tags[1].pk))}
            if has_public_tags
            else set()
        )
        assert all("style" not in badge.attrs for badge in badges)
        assert "Private planning" not in page.get_text()
        assert "Unused public tag" not in page.get_text()
        assert "#ef1234" not in files[name].decode()
        if has_public_tags:
            assert "Public &amp; &lt;tag&gt;" in files[name].decode()
        else:
            assert page.select_one('[data-rel="tags"]') is None
    css = files["assets/schedule.css"].decode()
    variables = files["assets/variables.css"].decode()
    assert "#ef1234" not in variables
    assert "#abc456" not in variables
    for tag in tags[:2]:
        assert (
            f'[data-tag="{tag.pk}"] {{\n    --tag-color: {tag.color};' in variables
        ) is has_public_tags
    assert "[data-tag] {" in css
    assert "color: var(--tag-text-color);" in css


@pytest.mark.parametrize(
    "color", ("invalid", "#12", "#123456; } body { display:none }")
)
def test_invalid_event_color_uses_the_default_without_a_variable_cycle(
    published_talk_slot, event, color
):
    with scope(event=event):
        event.primary_color = color
        event.save(update_fields=["primary_color"])
        css = StaticScheduleExporter(event).get_files()["assets/variables.css"].decode()
    assert color not in css
    assert "--color-primary: var(--color-pretalx);" in css
    assert "--color-primary: var(--color-primary);" not in css
    assert "--color-text-on-primary: var(--color-text);" in css


@pytest.mark.parametrize(
    ("tag_color", "primary", "foreground"),
    (
        ("#fff", "#123456", "var(--color-text)"),
        ("#123", "#ffffcc", "var(--color-white)"),
        ("", "#ffffcc", "var(--color-text)"),
        ("invalid", "#123456", "var(--color-white)"),
        ("", "invalid", "var(--color-text)"),
    ),
)
def test_public_tag_text_uses_a_contrasting_color(
    published_talk_slot, event, tag_color, primary, foreground
):
    with scope(event=event):
        event.primary_color = primary
        event.save(update_fields=["primary_color"])
        tag = Tag.objects.create(
            event=event, tag="Colored tag", color=tag_color, is_public=True
        )
        published_talk_slot.submission.tags.add(tag)
        css = StaticScheduleExporter(event).get_files()["assets/variables.css"].decode()
    color = tag_color if tag_color.startswith("#") else "var(--color-primary)"
    assert (
        f'[data-tag="{tag.pk}"] {{\n    --tag-color: {color};\n'
        f"    --tag-text-color: {foreground};" in css
    )

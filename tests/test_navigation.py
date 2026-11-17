import datetime as dt
import posixpath
from copy import deepcopy

import pytest
from bs4 import BeautifulSoup
from django_scopes import scope

from pretalx.schedule.models import Room
from pretalx.submission.models import Track

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("timezone", ("Europe/Berlin", "Pacific/Auckland"))
def test_navigation_is_consistent_on_every_page(
    published_talk_slot, event, day_files, timezone
):
    with scope(event=event):
        event.timezone = timezone
        event.save(update_fields=["timezone"])
        Track.objects.create(event=event, name="Empty & <track>", position=0)
        Room.objects.create(event=event, name="Empty & <room>", position=0)
        tracks = list(event.tracks.order_by("position", "pk"))
        rooms = list(event.rooms.order_by("position", "pk"))
        files = StaticScheduleExporter(event).get_files()

    destinations = {
        "day-navigation": ["index.html", *day_files],
        "track-navigation": [f"tracks/{track.pk}.html" for track in tracks],
        "room-navigation": [f"rooms/{room.pk}.html" for room in rooms],
    }
    for name, contents in files.items():
        if not name.endswith(".html"):
            continue
        page = BeautifulSoup(contents, "html.parser")
        navigation = page.select_one(".schedule-navigation")
        assert navigation is not None
        assert page.main.find(recursive=False) == navigation
        assert [group["class"][0] for group in navigation.find_all("nav")] == list(
            destinations
        )
        for selector, expected in destinations.items():
            group = navigation.select_one(f".{selector}")
            items = group.select("li")
            assert len(items) == len(expected)
            for item, destination in zip(items, expected, strict=True):
                if name == destination:
                    current = item.find(attrs={"aria-current": "page"})
                    assert current is not None
                    assert current.name == "strong"
                    assert not item.find("a")
                else:
                    link = item.find("a")
                    assert link is not None
                    assert not link["href"].startswith("/")
                    assert (
                        posixpath.normpath(
                            posixpath.join(posixpath.dirname(name), link["href"])
                        )
                        == destination
                    )
                    assert destination in files
                    assert not item.find(attrs={"aria-current": "page"})
            assert len(group.select("[aria-current='page']")) == int(name in expected)
        assert [
            item.get_text(strip=True)
            for item in navigation.select(".track-navigation li")
        ] == [str(track.name) for track in tracks]
        assert [
            item.get_text(strip=True)
            for item in navigation.select(".room-navigation li")
        ] == [str(room.name) for room in rooms]
        assert [
            time["datetime"] for time in navigation.select(".day-navigation time")
        ] == [
            (event.date_from + dt.timedelta(days=offset)).isoformat()
            for offset in range(event.duration)
        ]
        assert not navigation.find(["track", "room"])


def test_navigation_shows_empty_track_and_room_lists(
    published_schedule, event, day_files
):
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    for name in ("index.html", *day_files):
        page = BeautifulSoup(files[name], "html.parser")
        assert len(page.select(".schedule-navigation nav")) == 3
        assert "No tracks." in page.select_one(".track-navigation").get_text()
        assert "No rooms." in page.select_one(".room-navigation").get_text()
        assert not page.select(".track-navigation a, .room-navigation a")
        assert len(page.select(".day-navigation li")) == event.duration + 1


def test_shared_navigation_template_can_be_overridden(
    published_talk_slot, event, day_files, settings, tmp_path
):
    templates = deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"].insert(0, tmp_path)
    settings.TEMPLATES = templates
    override = tmp_path / "pretalx_static_schedule/navigation.html"
    override.parent.mkdir()
    override.write_text(
        '<nav class="custom-navigation">{% for day in navigation_days %}'
        '<a href="{{ asset_prefix }}{{ day.filename }}">{{ day.filename }}</a>'
        "{% endfor %}</nav>"
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    for name, contents in files.items():
        if not name.endswith(".html"):
            continue
        page = BeautifulSoup(contents, "html.parser")
        assert not page.select(".schedule-navigation")
        links = page.select(".custom-navigation a")
        assert len(links) == len(day_files)
        assert [
            posixpath.normpath(posixpath.join(posixpath.dirname(name), link["href"]))
            for link in links
        ] == day_files

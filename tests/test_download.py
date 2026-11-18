from io import BytesIO
from zipfile import ZipFile

import pytest
from django_scopes import scope
from freezegun import freeze_time

from pretalx.common.exporter import get_schedule_exporter_content
from pretalx.schedule.domain.release import freeze_schedule

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def test_zip_download_from_organiser_interface(
    published_talk_slot, published_speaker, event, orga_client, day_files
):
    exporter = StaticScheduleExporter(event)
    listing = orga_client.get(event.orga_urls.schedule_export)
    assert listing.status_code == 200
    assert any(
        isinstance(item, StaticScheduleExporter)
        for item in listing.context["exporters"]
    )
    assert f'href="{exporter.urls.base}"' in listing.content.decode("utf-8")

    with freeze_time("2026-01-15T12:30:00Z"):
        response = orga_client.get(exporter.urls.base)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        assert (
            response["Content-Disposition"]
            == f'attachment; filename="{exporter.filename}"'
        )

    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "index.html",
            "speakers/index.html",
            *day_files,
            f"tracks/{published_talk_slot.submission.track_id}.html",
            f"rooms/{published_talk_slot.room_id}.html",
            "sessions/TOOLS01.html",
            "speakers/ADA0001.html",
            "assets/schedule.css",
            "assets/variables.css",
        }
        assert "Schedule version 1.0" in archive.read("index.html").decode("utf-8")
        assert "Ada Example" in archive.read("sessions/TOOLS01.html").decode("utf-8")
        assert "Ada Example" in archive.read("speakers/ADA0001.html").decode("utf-8")


def test_zip_download_is_not_public(published_talk_slot, event, client):
    response = client.get(StaticScheduleExporter(event).urls.base)
    assert response.status_code == 404
    assert response["Content-Type"] != "application/zip"


def test_no_download_without_a_published_schedule(event, orga_client, caplog):
    response = orga_client.get(StaticScheduleExporter(event).urls.base)
    assert response.status_code == 404
    assert response["Content-Type"] != "application/zip"
    assert "No published schedule is available." in caplog.text


@pytest.mark.parametrize("schedule_type", ("wip", "archived"))
def test_api_rejects_non_current_schedules(
    published_schedule, event, orga_user, rf, caplog, schedule_type
):
    with scope(event=event):
        if schedule_type == "wip":
            schedule = event.wip_schedule
        else:
            freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
            schedule = published_schedule
        exporter = StaticScheduleExporter(event)
        request = rf.get(exporter.urls.base)
        request.event = event
        request.user = orga_user

        assert (
            get_schedule_exporter_content(request, exporter.identifier, schedule)
            is None
        )
    assert "Only the current published schedule can be exported." in caplog.text


def test_download_uses_the_latest_published_schedule(
    published_talk_slot, event, orga_client, day_files
):
    with scope(event=event):
        event.wip_schedule.talks.update(room=None)
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)

    response = orga_client.get(StaticScheduleExporter(event).urls.base)
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "index.html",
            "speakers/index.html",
            *day_files,
            f"tracks/{published_talk_slot.submission.track_id}.html",
            f"rooms/{published_talk_slot.room_id}.html",
            "assets/schedule.css",
            "assets/variables.css",
        }
        html = archive.read("index.html").decode("utf-8")
        assert "Schedule version 2.0" in html
        assert "Schedule version 1.0" not in html
        assert "Building &lt;tools&gt;" not in html

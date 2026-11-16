import datetime as dt

import pytest
from django.utils.timezone import now
from django_scopes import scope

from pretalx.schedule.domain.release import freeze_schedule

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def test_one_published_schedule(published_schedule, event):
    exporter = StaticScheduleExporter(event)

    with scope(event=event):
        assert exporter.schedule == published_schedule
        assert exporter.schedule is event.current_schedule


def test_multiple_published_schedules(published_schedule, event):
    with scope(event=event):
        latest, _ = freeze_schedule(event.wip_schedule, "0.5", notify_speakers=False)

        assert published_schedule.is_archived
        assert StaticScheduleExporter(event).schedule == latest


def test_newer_wip_schedule_is_not_selected(published_schedule, event):
    with scope(event=event):
        wip = event.wip_schedule

        assert wip.pk > published_schedule.pk
        assert wip.published is None
        assert StaticScheduleExporter(event).schedule == published_schedule


def test_older_archived_schedule_is_not_selected(published_schedule, event):
    with scope(event=event):
        archived = event.schedules.create(
            version="99.0", published=now() - dt.timedelta(days=1)
        )

        assert archived.pk > published_schedule.pk
        assert archived.is_archived
        assert StaticScheduleExporter(event).schedule == published_schedule


def test_no_published_schedule(event):
    with scope(event=event):
        assert event.wip_schedule.published is None
        assert event.current_schedule is None

        with pytest.raises(ValueError, match="No published schedule is available"):
            assert StaticScheduleExporter(event).schedule


def test_current_schedule_provided_by_pretalx(published_schedule, event):
    exporter = StaticScheduleExporter(event)

    with scope(event=event):
        supplied = event.schedules.get(pk=published_schedule.pk)
        exporter.schedule = supplied

        assert exporter.schedule == published_schedule
        assert exporter.schedule is event.current_schedule


@pytest.mark.parametrize("schedule_type", ("wip", "archived", "none"))
def test_non_current_schedule_provided_by_pretalx(
    published_schedule, event, schedule_type
):
    with scope(event=event):
        if schedule_type == "wip":
            supplied = event.wip_schedule
        elif schedule_type == "archived":
            freeze_schedule(event.wip_schedule, "0.5", notify_speakers=False)
            supplied = published_schedule
        else:
            supplied = None

        exporter = StaticScheduleExporter(event)
        exporter.schedule = supplied

        with pytest.raises(
            ValueError, match="Only the current published schedule can be exported"
        ):
            assert exporter.schedule


def test_provided_schedule_becomes_archived(published_schedule, event):
    exporter = StaticScheduleExporter(event)

    with scope(event=event):
        exporter.schedule = published_schedule
        assert exporter.schedule == published_schedule
        freeze_schedule(event.wip_schedule, "0.5", notify_speakers=False)

        with pytest.raises(
            ValueError, match="Only the current published schedule can be exported"
        ):
            assert exporter.schedule

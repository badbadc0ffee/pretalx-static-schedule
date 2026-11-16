import pytest
from django.urls import reverse

from pretalx_static_schedule.models import StaticScheduleSettings

SETTINGS_URL_NAME = "plugins:pretalx_static_schedule:settings"


@pytest.mark.django_db
def test_orga_can_access_settings(orga_client, event):
    response = orga_client.get(
        reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug}), follow=True
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_orga_can_save_settings(orga_client, event):
    url = reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug})
    response = orga_client.post(url, {"some_setting": "B"}, follow=True)
    assert response.status_code == 200
    assert StaticScheduleSettings.objects.get(event=event).some_setting == "B"


@pytest.mark.django_db
def test_generated_settings_lookup_and_string(event):
    assert StaticScheduleSettings.for_event(event) is None
    settings = StaticScheduleSettings.objects.create(event=event)
    assert StaticScheduleSettings.for_event(event) == settings
    assert str(settings) == f"StaticScheduleSettings(event={event})"

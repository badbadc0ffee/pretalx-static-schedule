from django.dispatch import receiver
from django.urls import reverse

from pretalx.orga.signals import nav_event_settings


@receiver(nav_event_settings)
def pretalx_static_schedule_settings(sender, request, **kwargs):
    return [
        {
            "label": "pretalx-static-schedule",
            "url": reverse(
                "plugins:pretalx_static_schedule:settings",
                kwargs={"event": request.event.slug},
            ),
            "active": request.resolver_match.url_name
            == "plugins:pretalx_static_schedule:settings",
        }
    ]

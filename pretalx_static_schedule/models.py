from django.db import models


class StaticScheduleSettings(models.Model):
    event = models.OneToOneField(
        to="event.Event",
        on_delete=models.CASCADE,
        related_name="pretalx_static_schedule_settings",
    )
    some_setting = models.CharField(max_length=10, default="A")

    def __str__(self):
        return f"StaticScheduleSettings(event={self.event})"

    @classmethod
    def for_event(cls, event):
        return cls.objects.filter(event=event).first()

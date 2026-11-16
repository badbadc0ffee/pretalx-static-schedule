from django.utils.translation import gettext_lazy as _

from pretalx.common.exporter import BaseExporter


class StaticScheduleExporter(BaseExporter):
    verbose_name = _("Static HTML (ZIP)")
    public = False
    icon = "fa-download"
    filename_identifier = "static-schedule"
    extension = "zip"
    content_type = "application/zip"

    @property
    def schedule(self):
        schedule = self.event.current_schedule
        if schedule is None:
            raise ValueError("No published schedule is available.")
        if getattr(self, "_schedule", schedule) != schedule:
            raise ValueError("Only the current published schedule can be exported.")
        return schedule

    @schedule.setter
    def schedule(self, schedule):
        # pretalx assigns the schedule outside its render error handler.
        self._schedule = schedule

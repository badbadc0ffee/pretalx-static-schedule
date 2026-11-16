from django.utils.translation import gettext_lazy as _

from pretalx.common.exporter import BaseExporter


class StaticScheduleExporter(BaseExporter):
    verbose_name = _("Static HTML (ZIP)")
    public = False
    icon = "fa-download"
    filename_identifier = "static-schedule"
    extension = "zip"
    content_type = "application/zip"

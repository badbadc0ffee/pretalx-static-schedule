from django.apps import AppConfig
from django.utils.translation import gettext_lazy

from . import __version__


class PluginApp(AppConfig):
    name = "pretalx_static_schedule"
    verbose_name = "pretalx-static-schedule"

    class PretalxPluginMeta:
        name = gettext_lazy("pretalx-static-schedule")
        author = "Florian Mösch"
        description = gettext_lazy(
            "Export the current pretalx schedule as static HTML pages."
        )
        visible = True
        version = __version__
        category = "EXPORTER"

    def ready(self):
        from . import signals  # noqa: PLC0415, F401

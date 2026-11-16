import pytest
from django.apps import apps
from django.urls import NoReverseMatch, reverse

from pretalx.common.exporter import BaseExporter
from pretalx.common.plugins import get_all_plugins
from pretalx.common.signals import register_data_exporters
from pretalx.event.domain.plugins import disable_plugin

from pretalx_static_schedule import __version__


def test_plugin_metadata():
    app = apps.get_app_config("pretalx_static_schedule")
    plugin = next(
        plugin
        for plugin in get_all_plugins()
        if plugin.module == "pretalx_static_schedule"
    )

    assert plugin is app.PretalxPluginMeta
    assert plugin.app is app
    assert str(plugin.name) == "pretalx-static-schedule"
    assert plugin.author == "Florian Mösch"
    assert str(plugin.description) == (
        "Export the current pretalx schedule as static HTML pages."
    )
    assert plugin.category == "EXPORTER"
    assert plugin.visible is True
    assert plugin.version == __version__
    assert not getattr(plugin, "settings_links", [])


@pytest.mark.django_db
@pytest.mark.parametrize("enabled", (True, False))
def test_exporter_registration(event, enabled):
    if not enabled:
        disable_plugin(event, "pretalx_static_schedule")

    exporters = [
        exporter
        for receiver, exporter in register_data_exporters.send(sender=event)
        if receiver.__module__ == "pretalx_static_schedule.signals"
    ]

    if enabled:
        assert len(exporters) == 1
        exporter = exporters[0]
        assert exporter.__module__ == "pretalx_static_schedule.exporter"
        assert exporter.__name__ == "StaticScheduleExporter"
        assert issubclass(exporter, BaseExporter)
        assert exporter(event).event is event
    else:
        assert exporters == []


@pytest.mark.django_db
def test_plugin_has_no_settings_page(event):
    with pytest.raises(NoReverseMatch):
        reverse(
            "plugins:pretalx_static_schedule:settings", kwargs={"event": event.slug}
        )

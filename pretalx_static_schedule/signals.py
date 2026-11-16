from django.dispatch import receiver

from pretalx.common.signals import register_data_exporters


@receiver(register_data_exporters, dispatch_uid="exporter_pretalx_static_schedule")
def register_data_exporter(sender, **kwargs):
    from .exporter import StaticScheduleExporter  # noqa: PLC0415

    return StaticScheduleExporter

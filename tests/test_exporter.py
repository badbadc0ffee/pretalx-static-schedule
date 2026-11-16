import pytest

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("attribute", "expected"),
    (
        ("verbose_name", "Static HTML (ZIP)"),
        ("filename_identifier", "static-schedule"),
        ("extension", "zip"),
        ("content_type", "application/zip"),
        ("icon", "fa-download"),
        ("public", False),
        ("group", "submission"),
    ),
)
def test_exporter_properties(event, attribute, expected):
    assert getattr(StaticScheduleExporter(event), attribute) == expected

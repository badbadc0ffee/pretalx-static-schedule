from copy import deepcopy
from pathlib import Path

import pytest
from django.apps import apps
from django.template.loader import get_template
from django_scopes import scope
from pytest_django.asserts import assertInHTML

from pretalx.submission.models import Answer, Question

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.fixture
def template_directory(settings, tmp_path):
    templates = deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"].insert(0, tmp_path)
    settings.TEMPLATES = templates
    return tmp_path


def test_default_templates(published_talk_slot, event):
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    template_root = (
        Path(apps.get_app_config("pretalx_static_schedule").path) / "templates"
    )
    assert Path(get_template("pretalx_static_schedule/base.html").origin.name) == (
        template_root / "pretalx_static_schedule/base.html"
    )
    assert Path(get_template("pretalx_static_schedule/answers.html").origin.name) == (
        template_root / "pretalx_static_schedule/answers.html"
    )
    for name, output, title in (
        ("schedule.html", "index.html", f"{event.name} – Schedule"),
        (
            "session.html",
            "sessions/TOOLS01.html",
            f"Building &lt;tools&gt; – {event.name}",
        ),
    ):
        template_name = f"pretalx_static_schedule/{name}"
        assert (
            Path(get_template(template_name).origin.name)
            == template_root / template_name
        )
        assertInHTML(f"<title>{title}</title>", files[output].decode("utf-8"))


@pytest.mark.parametrize(
    ("name", "output", "variable", "title"),
    (
        ("schedule.html", "index.html", "event.name", "Fancy testevent"),
        (
            "session.html",
            "sessions/TOOLS01.html",
            "submission.title",
            "Building &lt;tools&gt;",
        ),
    ),
)
def test_page_template_override(
    published_talk_slot, event, template_directory, name, output, variable, title
):
    template_name = f"pretalx_static_schedule/{name}"
    override = template_directory / template_name
    override.parent.mkdir()
    override.write_text(
        '<!DOCTYPE html><html lang="en"><head><title>Custom page</title></head>'
        "<body><h1>Custom {{ " + variable + " }}</h1>"
        "<p>Version {{ schedule.version }}</p></body></html>",
        encoding="utf-8",
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert Path(get_template(template_name).origin.name) == override
    html = files[output].decode("utf-8")
    assertInHTML(f"<h1>Custom {title}</h1>", html)
    assertInHTML("<p>Version 1.0</p>", html)
    assertInHTML("<title>Custom page</title>", html)


def test_base_override_changes_both_pages(
    published_talk_slot, event, template_directory
):
    override = template_directory / "pretalx_static_schedule/base.html"
    override.parent.mkdir()
    override.write_text(
        '<!DOCTYPE html><html lang="en"><head><title>{% block title %}{% endblock %}</title></head>'
        "<body><p>Custom site {{ event.name }}</p>"
        "<header>{% block header %}{% endblock %}</header>"
        "<main>{% block content %}{% endblock %}</main></body></html>",
        encoding="utf-8",
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert (
        Path(get_template("pretalx_static_schedule/base.html").origin.name) == override
    )
    for name, content in files.items():
        if not name.endswith(".html"):
            continue
        html = content.decode("utf-8")
        assertInHTML(f"<p>Custom site {event.name}</p>", html)
        assertInHTML(
            f'<time datetime="{published_talk_slot.start.isoformat()}">10:00</time>',
            html,
        )
        assert "Building &lt;tools&gt;" in html
    assertInHTML(
        f"<title>{event.name} – Schedule</title>", files["index.html"].decode("utf-8")
    )
    assertInHTML(
        f"<title>Building &lt;tools&gt; – {event.name}</title>",
        files["sessions/TOOLS01.html"].decode("utf-8"),
    )


@pytest.mark.parametrize(
    ("name", "output"),
    (("schedule.html", "index.html"), ("session.html", "sessions/TOOLS01.html")),
)
def test_override_extends_default_page(
    published_talk_slot, event, template_directory, name, output
):
    template_name = f"pretalx_static_schedule/{name}"
    override = template_directory / template_name
    override.parent.mkdir()
    override.write_text(
        '{% extends "' + template_name + '" %}'
        "{% block content %}{{ block.super }}<p>Custom note {{ event.name }}</p>{% endblock %}",
        encoding="utf-8",
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert Path(get_template(template_name).origin.name) == override
    html = files[output].decode("utf-8")
    assertInHTML(f"<p>Custom note {event.name}</p>", html)
    assert "Building &lt;tools&gt;" in html
    assertInHTML(
        f'<time datetime="{published_talk_slot.start.isoformat()}">10:00</time>', html
    )


def test_answers_template_override(published_talk_slot, event, template_directory):
    override = template_directory / "pretalx_static_schedule/answers.html"
    override.parent.mkdir()
    override.write_text(
        '<aside class="custom-answers">{% for answer in answers %}'
        "<p>{{ answer.question.question }}: {{ answer.answer }}</p>"
        "{% endfor %}</aside>",
        encoding="utf-8",
    )
    with scope(event=event):
        submission = published_talk_slot.submission
        for target, obj in (
            ("submission", submission),
            ("speaker", submission.sorted_speakers.first()),
        ):
            question = Question.objects.create(
                event=event,
                question=target,
                target=target,
                variant="text",
                is_public=True,
            )
            Answer.objects.create(
                question=question, answer="Public answer", **{target: obj}
            )
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assert (
        Path(get_template("pretalx_static_schedule/answers.html").origin.name)
        == override
    )
    assert html.count('class="custom-answers"') == 2
    for target in ("submission", "speaker"):
        assertInHTML(f"<p>{target}: Public answer</p>", html)

from copy import deepcopy
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.template.loader import get_template
from django.utils.formats import date_format
from django_scopes import scope
from pytest_django.asserts import assertInHTML

from pretalx.agenda.recording import BaseRecordingProvider
from pretalx.agenda.signals import html_above_session_pages, register_recording_provider
from pretalx.submission.models import Answer, Question, Tag

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
    assert Path(get_template("pretalx_static_schedule/recording.html").origin.name) == (
        template_root / "pretalx_static_schedule/recording.html"
    )
    for name in (
        "speaker_info.html",
        "speaker_summary.html",
        "session_summary.html",
        "session_body.html",
        "tags.html",
    ):
        assert Path(get_template(f"pretalx_static_schedule/{name}").origin.name) == (
            template_root / f"pretalx_static_schedule/{name}"
        )
    for name, output, title in (
        ("schedule.html", "index.html", f"All sessions – {event.name}"),
        (
            "session.html",
            "sessions/TOOLS01.html",
            f"Building &lt;tools&gt; – {event.name}",
        ),
        ("speaker.html", "speakers/ADA0001.html", f"Ada Example – {event.name}"),
        (
            "speaker_overview.html",
            "speakers/index.html",
            f"All speakers – {event.name}",
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
        (
            "speaker.html",
            "speakers/ADA0001.html",
            "speaker.profile.get_display_name",
            "Ada Example",
        ),
        (
            "speaker_overview.html",
            "speakers/index.html",
            "speakers.0.profile.get_display_name",
            "Ada Example",
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


def test_base_override_changes_all_pages(
    published_talk_slot, published_speaker, event, template_directory
):
    override = template_directory / "pretalx_static_schedule/base.html"
    override.parent.mkdir()
    override.write_text(
        '<!DOCTYPE html><html lang="en"><head><title>{% block title %}{{ page.title }} – {{ metadata.title }}{% endblock %}</title></head>'
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
    for name in ("index.html", "sessions/TOOLS01.html"):
        html = files[name].decode("utf-8")
        assertInHTML(
            f'<time datetime="{published_talk_slot.start.isoformat()}">{date_format(published_talk_slot.start.astimezone(event.tz), "F j, Y, H:i")}</time>',
            html,
        )
        assert "Building &lt;tools&gt;" in html
    speaker_html = files["speakers/ADA0001.html"].decode("utf-8")
    assertInHTML("<h1>Ada Example</h1>", speaker_html)
    assertInHTML(f"<title>Ada Example – {event.name}</title>", speaker_html)
    assertInHTML(
        f"<title>All sessions – {event.name}</title>",
        files["index.html"].decode("utf-8"),
    )
    assertInHTML(
        f"<title>Building &lt;tools&gt; – {event.name}</title>",
        files["sessions/TOOLS01.html"].decode("utf-8"),
    )


def test_shared_shell_blocks_can_extend_the_defaults(
    published_talk_slot, event, template_directory
):
    override = template_directory / "pretalx_static_schedule/base.html"
    override.parent.mkdir()
    override.write_text(
        '{% extends "pretalx_static_schedule/base.html" %}'
        "{% block title %}Custom {{ block.super }}{% endblock %}"
        "{% block header %}{{ block.super }}<p>Header addition</p>{% endblock %}"
        "{% block navigation %}{{ block.super }}<p>Navigation addition</p>{% endblock %}"
        "{% block footer %}{{ block.super }}<p>Footer addition</p>{% endblock %}"
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()
    for name, content in files.items():
        if not name.endswith(".html"):
            continue
        page = BeautifulSoup(content, "html.parser")
        assert page.title.get_text().startswith("Custom ")
        assert page.header.find("p", string="Header addition")
        assert page.header.find("p", string="Navigation addition")
        assert page.header.nav.find("a", string="All sessions")
        assert page.footer.find("p", string="Footer addition")
        assert page.footer.find("a", string="Source schedule")


@pytest.mark.parametrize("external_first", (False, True))
def test_intentionally_external_links_keep_their_original_destinations(
    published_talk_slot, event, template_directory, external_first
):
    override = template_directory / "pretalx_static_schedule/schedule.html"
    override.parent.mkdir()
    attributes = (
        'rel="noopener external" href="{{ event.urls.schedule.full }}"'
        if external_first
        else 'href="{{ event.urls.schedule.full }}" rel="external noopener"'
    )
    override.write_text(
        '{% extends "pretalx_static_schedule/schedule.html" %}'
        "{% block content %}{{ block.super }}<a "
        + attributes
        + ">External schedule</a>"
        '<a href="{{ event.urls.schedule.full }}">Local schedule</a>{% endblock %}'
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()
    page = BeautifulSoup(files["index.html"], "html.parser")
    assert (
        page.main.find("a", string="External schedule")["href"]
        == event.urls.schedule.full()
    )
    assert page.main.find("a", string="Local schedule")["href"] == "index.html"


@pytest.mark.parametrize(
    ("name", "output"),
    (
        ("schedule.html", "index.html"),
        ("session.html", "sessions/TOOLS01.html"),
        ("speaker.html", "speakers/ADA0001.html"),
        ("speaker_overview.html", "speakers/index.html"),
    ),
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
    if name == "speaker_overview.html":
        assertInHTML('<a href="ADA0001.html">Ada Example</a>', html)
    else:
        assert "Building &lt;tools&gt;" in html
        assertInHTML(
            f'<time datetime="{published_talk_slot.start.isoformat()}">{date_format(published_talk_slot.start.astimezone(event.tz), "F j, Y, H:i")}</time>',
            html,
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
        files = StaticScheduleExporter(event).get_files()

    assert (
        Path(get_template("pretalx_static_schedule/answers.html").origin.name)
        == override
    )
    html = files["sessions/TOOLS01.html"].decode("utf-8")
    assert html.count('class="custom-answers"') == 2
    for target in ("submission", "speaker"):
        assertInHTML(f"<p>{target}: Public answer</p>", html)
    speaker_html = files["speakers/ADA0001.html"].decode("utf-8")
    assert speaker_html.count('class="custom-answers"') == 1
    assertInHTML("<p>speaker: Public answer</p>", speaker_html)
    assert "submission: Public answer" not in speaker_html


@pytest.mark.parametrize(
    "page", ("sessions/TOOLS01.html", "speakers/ADA0001.html", "speakers/index.html")
)
def test_speaker_information_template_override(
    published_speaker, event, template_directory, page
):
    template_name = "pretalx_static_schedule/speaker_info.html"
    override = template_directory / template_name
    override.parent.mkdir()
    override.write_text(
        '<aside class="custom-speaker"><p>Custom {{ speaker.profile.get_display_name }}</p></aside>',
        encoding="utf-8",
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert Path(get_template(template_name).origin.name) == override
    html = files[page].decode("utf-8")
    assertInHTML(
        '<aside class="custom-speaker"><p>Custom Ada Example</p></aside>', html
    )
    assert 'class="biography"' not in html


@pytest.mark.parametrize("page", ("sessions/TOOLS01.html", "speakers/index.html"))
def test_speaker_summary_template_override(
    published_speaker, event, template_directory, page
):
    template_name = "pretalx_static_schedule/speaker_summary.html"
    override = template_directory / template_name
    override.parent.mkdir()
    override.write_text(
        '<section class="custom-summary"><h2>{{ speaker.profile.get_display_name }}</h2>'
        "<p>Talk count: {{ speaker.submission_count }}</p></section>"
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert Path(get_template(template_name).origin.name) == override
    html = files[page].decode("utf-8")
    assertInHTML(
        '<section class="custom-summary"><h2>Ada Example</h2><p>Talk count: 1</p></section>',
        html,
    )
    assert 'class="biography"' not in html


@pytest.mark.parametrize(
    ("page", "prefix"),
    (("index.html", "sessions/"), ("speakers/ADA0001.html", "../sessions/")),
)
def test_session_summary_template_override(
    published_speaker, event, template_directory, page, prefix
):
    template_name = "pretalx_static_schedule/session_summary.html"
    override = template_directory / template_name
    override.parent.mkdir()
    override.write_text(
        '<article class="custom-session"><a href="{{ page.asset_prefix }}{{ session.filename }}">'
        "{{ session.title }}</a></article>",
        encoding="utf-8",
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert Path(get_template(template_name).origin.name) == override
    html = files[page].decode("utf-8")
    assertInHTML(
        f'<article class="custom-session"><a href="{prefix}TOOLS01.html">Building &lt;tools&gt;</a></article>',
        html,
    )
    assert 'class="session"' not in html


def test_recording_template_override(
    published_talk_slot, event, template_directory, register_signal_handler
):
    class Provider(BaseRecordingProvider):
        def get_recording(self, submission):
            return {
                "iframe": '<iframe src="https://video.example.org/watch/123" title="Session video"></iframe>',
                "csp_header": "https://video.example.org",
            }

    def handler(signal, sender, **kwargs):
        return Provider(sender)

    register_signal_handler(register_recording_provider, handler)
    template_name = "pretalx_static_schedule/recording.html"
    override = template_directory / template_name
    override.parent.mkdir()
    override.write_text(
        '<aside class="custom-recording"><h2>{{ submission.title }}</h2>'
        '{% for link in recording.links %}<a href="{{ link.url }}">'
        "Custom {{ link.label }}</a>{% endfor %}</aside>",
        encoding="utf-8",
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    assert Path(get_template(template_name).origin.name) == override
    html = files["sessions/TOOLS01.html"].decode("utf-8")
    assertInHTML(
        '<aside class="custom-recording"><h2>Building &lt;tools&gt;</h2>'
        '<a href="https://video.example.org/watch/123">Custom Session video</a></aside>',
        html,
    )


def test_custom_session_template_can_render_plugin_information(
    published_talk_slot, event, template_directory, register_signal_handler
):
    def handler(signal, sender, **kwargs):
        return "<p>Useful plugin information.</p><script>loadWidget()</script>"

    register_signal_handler(html_above_session_pages, handler)
    override = template_directory / "pretalx_static_schedule/session.html"
    override.parent.mkdir()
    override.write_text(
        '{% extends "pretalx_static_schedule/base.html" %}{% load static_schedule %}'
        "{% block content %}<h1>Custom {{ submission.title }}</h1>"
        '<aside>{% session_content "pretalx.agenda.signals.html_above_session_pages" %}'
        "</aside>{% endblock %}",
        encoding="utf-8",
    )
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    assertInHTML("<h1>Custom Building &lt;tools&gt;</h1>", html)
    assertInHTML("<aside><p>Useful plugin information.</p></aside>", html)
    assert "<script" not in html
    assert "loadWidget" not in html


@pytest.mark.parametrize(
    "page", ("index.html", "sessions/TOOLS01.html", "speakers/ADA0001.html")
)
def test_tags_template_override(published_talk_slot, event, template_directory, page):
    template_name = "pretalx_static_schedule/tags.html"
    override = template_directory / template_name
    override.parent.mkdir()
    override.write_text(
        '{% load static_schedule %}<aside class="custom-tags">'
        "{% for tag in submission|public_tags %}<p>{{ tag.tag }}</p>{% endfor %}</aside>",
        encoding="utf-8",
    )
    with scope(event=event):
        published_talk_slot.submission.tags.add(
            Tag.objects.create(
                event=event, tag="Public tag", color="#123abc", is_public=True
            ),
            Tag.objects.create(event=event, tag="Private tag", color="#ef1234"),
        )
        files = StaticScheduleExporter(event).get_files()

    assert Path(get_template(template_name).origin.name) == override
    html = files[page].decode()
    assertInHTML('<aside class="custom-tags"><p>Public tag</p></aside>', html)
    assert "Private tag" not in html
    assert 'class="tag-badge"' not in html

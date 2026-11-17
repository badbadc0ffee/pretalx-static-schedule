import posixpath
from io import BytesIO
from urllib.parse import quote, unquote, urljoin, urlsplit
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from django.http import HttpRequest
from django.utils.translation import get_language
from django_scopes import scope
from pytest_django.asserts import assertInHTML

from pretalx.agenda.signals import html_above_session_pages, html_below_session_pages
from pretalx.common.views.redirect import safelink
from pretalx.event.domain.plugins import disable_plugin
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.models import TalkSlot

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


def test_public_session_notes_are_exported(published_talk_slot, event):
    with scope(event=event):
        event.display_settings["texts"] = {
            "agenda_session_above": "**Before the session**",
            "agenda_session_below": "**After the session**",
        }
        event.save(update_fields=["display_settings"])
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    assertInHTML("<p><strong>Before the session</strong></p>", html)
    assertInHTML("<p><strong>After the session</strong></p>", html)
    assert html.index("Before the session") < html.index("<h1>")
    assert html.index("After the session") > html.index('class="description"')
    assert html.index("After the session") < html.index('class="speaker"')
    assert "Before the session" not in files["index.html"].decode("utf-8")


def test_session_hooks_receive_an_anonymous_public_request(
    published_talk_slot, event, register_signal_handler, orga_client
):
    seen = []

    def handler(signal, sender, request, submission, **kwargs):
        assert isinstance(request, HttpRequest)
        assert request.event == sender == event
        assert request.user.is_anonymous
        assert request.method == "GET"
        assert request.build_absolute_uri() == submission.urls.public.full()
        assert request.path == str(submission.urls.public)
        assert request.GET == {}
        assert request.COOKIES == {}
        assert request.session == {}
        assert get_language() == request.LANGUAGE_CODE
        seen.append((signal, submission.code))
        return '<p class="plugin-note">Public <strong>plugin information</strong>.</p>'

    register_signal_handler(html_above_session_pages, handler)
    register_signal_handler(html_below_session_pages, handler)
    orga_client.cookies["private"] = "Organiser cookie"
    response = orga_client.get(StaticScheduleExporter(event).urls.base)

    assert response.status_code == 200
    assert seen == [
        (html_above_session_pages, "TOOLS01"),
        (html_below_session_pages, "TOOLS01"),
    ]
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()
    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert len(page.select(".plugin-note")) == 2
    assert all(
        note.find("strong").get_text() == "plugin information"
        for note in page.select(".plugin-note")
    )


def test_empty_session_hooks_add_no_content(
    published_talk_slot, event, register_signal_handler
):
    def handler(signal, sender, **kwargs):
        return None

    register_signal_handler(html_above_session_pages, handler)
    register_signal_handler(html_below_session_pages, handler)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()
    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert page.find("h1").get_text() == "Building <tools>"
    assert not page.find(["script", "form", "iframe"])


def test_plugin_information_keeps_formatting_and_removes_interactions(
    published_talk_slot, event, register_signal_handler
):
    def handler(signal, sender, **kwargs):
        return (
            '<section id="plugin-info"><h2>Equipment</h2>'
            '<p onclick="loadEquipment()">Bring a <strong>laptop</strong>.</p>'
            "<ul><li>Power outlets</li><li>Wi-Fi</li></ul>"
            "<table><tbody><tr><th>Capacity</th><td>40</td></tr></tbody></table>"
            '<a href="https://example.org/handout" onmouseover="track()">Handout</a>'
            '<script src="/static/plugin.js">loadSecret()</script>'
            "<style>.plugin-info {display: none}</style>"
            '<form action="/private/"><label>Private input</label><input name="token" value="secret"></form>'
            '<button onclick="loadFeedback()">Load feedback</button>'
            '<iframe src="https://example.org/widget"></iframe>'
            "<p hidden>Hidden content</p>"
            '<a href="javascript:loadFeedback()">Open feedback widget</a>'
            "</section>"
        )

    register_signal_handler(html_above_session_pages, handler)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    page = BeautifulSoup(html, "html.parser")
    section = page.find(id="plugin-info")
    assertInHTML("<h2>Equipment</h2>", str(section))
    assertInHTML("<p>Bring a <strong>laptop</strong>.</p>", str(section))
    assertInHTML("<ul><li>Power outlets</li><li>Wi-Fi</li></ul>", str(section))
    assert section.find("th").get_text() == "Capacity"
    assert section.find("td").get_text() == "40"
    assert section.find("a", href="https://example.org/handout").get_text() == "Handout"
    assert not section.find(["script", "style", "form", "input", "button", "iframe"])
    assert not page.find(["script", "form", "input", "button", "iframe"])
    assert not any(
        name.startswith("on") for tag in page.find_all(True) for name in tag.attrs
    )
    for text in (
        "loadSecret",
        "Private input",
        "secret",
        "Load feedback",
        "Hidden content",
        "Open feedback widget",
    ):
        assert text not in html
    assert not any(name.endswith(".js") for name in files)


@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net"))
def test_plugin_links_are_static_and_preserve_external_information(
    published_talk_slot, event, register_signal_handler, custom_domain
):
    with scope(event=event):
        event.custom_domain = custom_domain
        event.save(update_fields=["custom_domain"])
        submission = published_talk_slot.submission
        speaker = submission.sorted_speakers.first()
        event.display_settings["texts"] = {
            "agenda_session_above": "[Public document](https://example.org/document?format=pdf&language=en)"
        }
        event.save(update_fields=["display_settings"])

    def handler(signal, sender, **kwargs):
        return (
            f"<a href='{event.urls.schedule}'>Schedule</a>"
            f"<a href='{submission.urls.public.full()}'>Session</a>"
            f"<a href='{speaker.urls.public}'>Speaker</a>"
            "<p id='plugin-details'>Equipment is provided.</p>"
            "<a href='#plugin-details'>Equipment details</a>"
            "<a href='../../../handouts/document.pdf'>Other public document</a>"
            "<a href='mailto:crew@example.org'>Contact crew</a>"
            f"<a href='{safelink('https://example.org/video')}'>Video</a>"
            "<a href='https://external.example.org/redirect/?url=123'>External redirect</a>"
        )

    register_signal_handler(html_below_session_pages, handler)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    for label, target in (
        ("Schedule", "../index.html"),
        ("Session", "TOOLS01.html"),
        ("Speaker", f"../speakers/{speaker.code}.html"),
        ("Equipment details", "#plugin-details"),
        (
            "Other public document",
            urljoin(submission.urls.public.full(), "../../../handouts/document.pdf"),
        ),
        ("Contact crew", "mailto:crew@example.org"),
        ("Public document", "https://example.org/document?format=pdf&language=en"),
        ("Video", "https://example.org/video"),
        ("External redirect", "https://external.example.org/redirect/?url=123"),
    ):
        assert page.find("a", string=label)["href"] == target
    assert page.find(id="plugin-details").get_text() == "Equipment is provided."


def test_plugin_links_omit_online_actions(
    published_talk_slot, event, register_signal_handler
):
    submission = published_talk_slot.submission

    def handler(signal, sender, **kwargs):
        return (
            "".join(
                f'<a href="{url.full()}?next=/private/">{label}</a>'
                for label, url in (
                    ("Send feedback", submission.urls.feedback),
                    ("Edit submission", submission.urls.user_base),
                    ("Sign up", submission.urls.signup),
                    ("Cancel signup", submission.urls.signup_cancel),
                    ("Add to calendar", submission.urls.ical),
                    ("Log in", event.urls.login),
                )
            )
            + "<p>Bring your own laptop.</p>"
        )

    register_signal_handler(html_below_session_pages, handler)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    for label in (
        "Send feedback",
        "Edit submission",
        "Sign up",
        "Cancel signup",
        "Add to calendar",
        "Log in",
    ):
        assert label not in html
    assertInHTML("<p>Bring your own laptop.</p>", html)


@pytest.mark.parametrize("url", ("/redirect/", "/redirect/?url=invalid"))
def test_invalid_pretalx_redirects_keep_the_label_without_a_broken_link(
    published_talk_slot, event, register_signal_handler, url
):
    def handler(signal, sender, **kwargs):
        return f'<p><a href="{url}">Reference document</a></p>'

    register_signal_handler(html_below_session_pages, handler)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert page.find("p", string="Reference document")
    assert not page.find("a", string="Reference document")


@pytest.mark.parametrize("absolute", (False, True))
def test_plugin_images_are_included_and_links_work_after_extraction(
    image_uploads, event, register_signal_handler, settings, tmp_path, absolute
):
    uploads, image_data = image_uploads
    static_root = tmp_path / "static"
    diagram = static_root / "plugin/diagram.svg"
    diagram.parent.mkdir(parents=True)
    diagram.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>Equipment diagram</text></svg>'
    )
    settings.STATICFILES_DIRS = [*settings.STATICFILES_DIRS, static_root]
    photo_url = uploads["session"].url
    diagram_url = settings.STATIC_URL + "plugin/diagram.svg"
    if absolute:
        photo_url = urljoin(settings.SITE_URL, photo_url)
        diagram_url = urljoin(settings.SITE_URL, diagram_url)

    def handler(signal, sender, **kwargs):
        return (
            f'<div id="plugin-images"><a href="{diagram_url}">Full diagram</a>'
            f'<img src="{photo_url}" alt="Equipment photo">'
            f'<img src="{diagram_url}" alt="Equipment diagram"></div>'
        )

    register_signal_handler(html_above_session_pages, handler)
    with scope(event=event):
        _, _, data = StaticScheduleExporter(event).render(request=None)

    extracted = tmp_path / "site"
    with ZipFile(BytesIO(data)) as archive:
        archive.extractall(extracted)
        assert archive.read("assets/plugin/diagram.svg") == diagram.read_bytes()
        assert archive.read(f"media/{uploads['session'].name}") == image_data
    page = BeautifulSoup(
        (extracted / "sessions/TOOLS01.html").read_text(), "html.parser"
    )
    section = page.find(id="plugin-images")
    assert section.find("img", alt="Equipment photo")["src"] == "../" + quote(
        f"media/{uploads['session'].name}"
    )
    assert (
        section.find("img", alt="Equipment diagram")["src"]
        == "../assets/plugin/diagram.svg"
    )
    assert (
        section.find("a", string="Full diagram")["href"]
        == "../assets/plugin/diagram.svg"
    )
    for deployment in ("/", "/conference/2026/"):
        for element in page.select("[href], [src]"):
            reference = element.get("href", element.get("src"))
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc:
                assert reference == "https://example.org"
                continue
            assert not reference.startswith("/")
            resolved = urlsplit(
                urljoin(
                    f"https://static.example.org{deployment}sessions/TOOLS01.html",
                    reference,
                )
            )
            assert resolved.path.startswith(deployment)
            target = posixpath.normpath(unquote(resolved.path.removeprefix(deployment)))
            assert (extracted / target).is_file()


@pytest.mark.parametrize("label", ("Equipment diagram", ""))
def test_remote_plugin_images_become_links(
    published_talk_slot,
    published_speaker,
    event,
    register_signal_handler,
    label,
    day_files,
):
    def handler(signal, sender, **kwargs):
        return (
            f'<p><img src="https://external.example.org/diagram.png" alt="{label}"></p>'
        )

    register_signal_handler(html_above_session_pages, handler)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert page.find(
        "a", href="https://external.example.org/diagram.png"
    ).get_text() == (label or "Image")
    assert not page.find("img", src="https://external.example.org/diagram.png")
    assert set(files) == {
        "index.html",
        *day_files,
        f"tracks/{published_talk_slot.submission.track_id}.html",
        "assets/schedule.css",
        "assets/variables.css",
        "sessions/TOOLS01.html",
        "speakers/ADA0001.html",
    }


@pytest.mark.parametrize("custom_domain", (None, "https://schedule.example.net:8443"))
def test_session_hooks_receive_public_page_routing_and_event_context(
    published_talk_slot, event, register_signal_handler, custom_domain
):
    with scope(event=event):
        event.custom_domain = custom_domain
        event.save(update_fields=["custom_domain"])

    def handler(signal, sender, request, submission, **kwargs):
        assert request.resolver_match.view_name == "agenda:talk"
        assert request.resolver_match.kwargs == {
            "event": event.slug,
            "slug": submission.code,
        }
        assert request.timezone == event.timezone
        assert request.uses_custom_domain == bool(custom_domain)
        assert request.host == urlsplit(submission.urls.public.full()).hostname
        assert request.port == urlsplit(submission.urls.public.full()).port
        assert request.build_absolute_uri() == submission.urls.public.full()
        return "<p>Public route context.</p>"

    register_signal_handler(html_above_session_pages, handler)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()
    assertInHTML(
        "<p>Public route context.</p>", files["sessions/TOOLS01.html"].decode("utf-8")
    )


@pytest.mark.parametrize("enabled", (False, True))
def test_only_enabled_plugins_add_session_information(
    published_talk_slot, event, register_signal_handler, enabled
):
    def handler(signal, sender, **kwargs):
        return '<p id="plugin-enabled">Enabled plugin information.</p>'

    register_signal_handler(html_above_session_pages, handler)
    handler.__module__ = "pretalx_static_schedule.test_content"
    with scope(event=event):
        if not enabled:
            disable_plugin(event, "pretalx_static_schedule")
        files = StaticScheduleExporter(event).get_files()

    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert bool(page.find(id="plugin-enabled")) == enabled


def test_archived_and_wip_only_sessions_do_not_call_content_hooks(
    published_talk_slot, event, register_signal_handler, day_files
):
    seen = []

    def handler(signal, sender, submission, **kwargs):
        seen.append(submission.code)
        return "<p>Session information.</p>"

    register_signal_handler(html_above_session_pages, handler)
    register_signal_handler(html_below_session_pages, handler)
    with scope(event=event):
        event.wip_schedule.talks.update(room=None)
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        event.wip_schedule.talks.update(room=published_talk_slot.room)
        files = StaticScheduleExporter(event).get_files()

    assert seen == []
    assert set(files) == {
        "index.html",
        f"tracks/{published_talk_slot.submission.track_id}.html",
        "assets/schedule.css",
        "assets/variables.css",
        *day_files,
    }
    assert "Schedule version 2.0" in files["index.html"].decode("utf-8")


def test_repeated_published_sessions_call_each_hook_once(
    published_talk_slot, event, register_signal_handler
):
    seen = []

    def handler(signal, sender, request, submission, **kwargs):
        seen.append((signal, submission.code, request.event.current_schedule.version))
        return "<p>Current session information.</p>"

    register_signal_handler(html_above_session_pages, handler)
    register_signal_handler(html_below_session_pages, handler)
    with scope(event=event):
        TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=published_talk_slot.submission,
            room=published_talk_slot.room,
            start=published_talk_slot.end,
            end=published_talk_slot.end
            + (published_talk_slot.end - published_talk_slot.start),
        )
        freeze_schedule(event.wip_schedule, "2.0", notify_speakers=False)
        files = StaticScheduleExporter(event).get_files()

    assert seen == [
        (html_above_session_pages, "TOOLS01", "2.0"),
        (html_below_session_pages, "TOOLS01", "2.0"),
    ]
    page = BeautifulSoup(files["sessions/TOOLS01.html"].decode("utf-8"), "html.parser")
    assert len(page.select(".session-times > li")) == 2
    assert len(page.find_all("p", string="Current session information.")) == 2


def test_plugin_image_inside_a_script_action_is_omitted(
    published_talk_slot, event, register_signal_handler
):
    def handler(signal, sender, **kwargs):
        return (
            '<a href="javascript:loadWidget()"><img src="https://example.org/widget.png" alt="Open widget"></a>'
            "<p>Public information.</p>"
        )

    register_signal_handler(html_above_session_pages, handler)
    with scope(event=event):
        files = StaticScheduleExporter(event).get_files()

    html = files["sessions/TOOLS01.html"].decode("utf-8")
    assertInHTML("<p>Public information.</p>", html)
    assert "loadWidget" not in html
    assert "Open widget" not in html

import posixpath
import re
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.utils.html import escape
from django.utils.translation import gettext_lazy as _
from django.utils.translation import override

from pretalx.common.exporter import BaseExporter

from .rendering import ScheduleRenderData

FONT_FILES = (
    "jersey-10-latin-ext-400-normal.woff2",
    "jersey-10-latin-400-normal.woff2",
    "jersey-10_License.txt",
    "Hack-Regular.woff2",
    "Hack-Bold.woff2",
    "Hack-Italic.woff2",
    "Hack-BoldItalic.woff2",
    "Hack-License.txt",
)


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

    def render(self, request, **kwargs):
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in self.get_files().items():
                archive.writestr(name, content)
        return self.filename, self.content_type, output.getvalue()

    @override("en")
    def get_files(self):
        schedule = self.schedule
        data = ScheduleRenderData(self.event, schedule)
        links = (
            {
                self.event.urls.schedule: "index.html",
                self.event.urls.schedule_nojs: "index.html",
                self.event.urls.speakers: "speakers/index.html",
            }
            | {
                session["submission"].urls.public: session["filename"]
                for session in data.sessions.values()
            }
            | {
                speaker["profile"].urls.public: speaker["filename"]
                for speaker in data.speakers.values()
            }
        )
        links |= {url.full(): destination for url, destination in links.items()}
        files = {
            "assets/schedule.css": Path(
                finders.find("pretalx_static_schedule/schedule.css")
            ).read_bytes(),
            "assets/variables.css": Path(
                finders.find("common/css/_variables.css")
            ).read_bytes()
            + render_to_string(
                "pretalx_static_schedule/event_variables.css",
                {
                    "event": self.event,
                    "tracks": data.tracks,
                    "tags": {
                        tag.pk: tag
                        for session in data.sessions.values()
                        for tag in session["tags"]
                    }.values(),
                },
            ).encode("utf-8"),
        }
        for name in FONT_FILES:
            files[f"assets/fonts/{name}"] = Path(
                finders.find(f"pretalx_static_schedule/fonts/{name}")
            ).read_bytes()
        context = {
            "event": self.event,
            "schedule": schedule,
            "metadata": data.metadata,
            "days": data.days,
            "asset_prefix": "",
            "files": files,
            "links": links,
            "recording_urls": {
                urlsplit(link["url"])._replace(query="", fragment="").geturl()
                for session in data.sessions.values()
                for link in session["recording"]["links"]
            },
        }
        html_files = []
        for page in data.pages():
            page_context = context | page | {"page": page}
            if page["kind"] == "speakers":
                page_context["speakers"] = page["items"]
            name = page["filename"]
            files[name] = render_to_string(
                f"pretalx_static_schedule/{page['template']}", page_context
            ).encode("utf-8")
            html_files.append(name)
        for name in html_files:
            directory = posixpath.dirname(name) or "."

            def rewrite_link(match, directory=directory):
                url = unescape(match[2])
                if url not in links or re.search(
                    r'\brel="[^"]*\bexternal\b[^"]*"', match[1] + match[3]
                ):
                    return match[0]
                path = posixpath.relpath(links[url], directory)
                return f"{match[1]}{escape(path)}{match[3]}"

            files[name] = re.sub(
                r'(<a\b[^>]*\bhref=")([^"]*)("[^>]*>)',
                rewrite_link,
                files[name].decode("utf-8"),
            ).encode("utf-8")
        return files

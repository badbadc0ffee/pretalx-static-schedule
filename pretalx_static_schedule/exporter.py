import posixpath
import re
from html import unescape
from pathlib import Path

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.utils.html import escape
from django.utils.translation import gettext_lazy as _

from pretalx.common.exporter import BaseExporter
from pretalx.schedule.interfaces.exporters import ScheduleData


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

    def get_files(self):
        schedule = self.schedule
        days = list(ScheduleData(self.event, schedule=schedule, with_breaks=True).data)
        sessions = {}
        for day in days:
            day["talks"] = sorted(
                [slot for room in day["rooms"] for slot in room["talks"]],
                key=lambda slot: slot.start,
            )
            for slot in day["talks"]:
                if slot.submission:
                    sessions.setdefault(slot.submission.code, []).append(slot)
        links = {
            self.event.urls.schedule: "index.html",
            self.event.urls.schedule_nojs: "index.html",
        } | {
            slots[0].submission.urls.public: f"sessions/{code}.html"
            for code, slots in sessions.items()
        }
        links |= {url.full(): destination for url, destination in links.items()}
        files = {
            "assets/schedule.css": Path(
                finders.find("pretalx_static_schedule/schedule.css")
            ).read_bytes()
        }
        context = {
            "event": self.event,
            "schedule": schedule,
            "days": days,
            "asset_prefix": "",
            "files": files,
            "links": links,
        }
        files["index.html"] = render_to_string(
            "pretalx_static_schedule/schedule.html", context
        ).encode("utf-8")
        for code, slots in sessions.items():
            submission = slots[0].submission
            speakers = [
                {
                    "profile": speaker,
                    "other_submissions": [
                        other[0].submission
                        for other in sessions.values()
                        if other[0].submission != submission
                        and speaker in other[0].submission.sorted_speakers
                    ],
                }
                for speaker in submission.sorted_speakers
            ]
            files[f"sessions/{code}.html"] = render_to_string(
                "pretalx_static_schedule/session.html",
                context
                | {
                    "submission": submission,
                    "slots": slots,
                    "speakers": speakers,
                    "asset_prefix": "../",
                },
            ).encode("utf-8")
        for name in ["index.html", *(f"sessions/{code}.html" for code in sessions)]:
            directory = posixpath.dirname(name) or "."

            def rewrite_link(match, directory=directory):
                url = unescape(match[2])
                if url not in links:
                    return match[0]
                path = posixpath.relpath(links[url], directory)
                return f"{match[1]}{escape(path)}{match[3]}"

            files[name] = re.sub(
                r'(<a\b[^>]*\bhref=")([^"]*)(")',
                rewrite_link,
                files[name].decode("utf-8"),
            ).encode("utf-8")
        return files

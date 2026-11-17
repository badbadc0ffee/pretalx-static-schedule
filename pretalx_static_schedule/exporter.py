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

from pretalx.common.exporter import BaseExporter
from pretalx.schedule.interfaces.exporters import ScheduleData
from pretalx.submission.domain.queries.question import public_answers_for_speaker

from .recording import recording_data


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

    def get_files(self):
        schedule = self.schedule
        days = list(ScheduleData(self.event, schedule=schedule, with_breaks=True).data)
        sessions = {}
        for day in days:
            day["filename"] = f"days/{day['start']:%y-%m-%d}.html"
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
        for slots in sessions.values():
            for speaker in slots[0].submission.sorted_speakers:
                links[speaker.urls.public] = f"speakers/{speaker.code}.html"
        recordings = {
            code: recording_data(slots[0].submission)
            for code, slots in sessions.items()
        }
        tracks = list(self.event.tracks.order_by("position", "pk"))
        rooms = list(self.event.rooms.order_by("position", "pk"))
        links |= {url.full(): destination for url, destination in links.items()}
        files = {
            "assets/schedule.css": Path(
                finders.find("pretalx_static_schedule/schedule.css")
            ).read_bytes(),
            "assets/variables.css": Path(
                finders.find("common/css/_variables.css")
            ).read_bytes(),
        }
        context = {
            "event": self.event,
            "schedule": schedule,
            "days": days,
            "navigation_days": days,
            "tracks": tracks,
            "rooms": rooms,
            "asset_prefix": "",
            "files": files,
            "links": links,
            "recording_urls": {
                urlsplit(link["url"])._replace(query="", fragment="").geturl()
                for recording in recordings.values()
                for link in recording["links"]
            },
        }
        files["index.html"] = render_to_string(
            "pretalx_static_schedule/schedule.html", context | {"is_overview": True}
        ).encode("utf-8")
        for day in days:
            files[day["filename"]] = render_to_string(
                "pretalx_static_schedule/schedule.html",
                context | {"days": [day], "overview_day": day, "asset_prefix": "../"},
            ).encode("utf-8")
        for track in tracks:
            track_days = []
            for day in days:
                talks = [
                    slot
                    for slot in day["talks"]
                    if slot.submission and slot.submission.track_id == track.pk
                ]
                if talks:
                    track_days.append(day | {"talks": talks})
            files[f"tracks/{track.pk}.html"] = render_to_string(
                "pretalx_static_schedule/track.html",
                context | {"track": track, "days": track_days, "asset_prefix": "../"},
            ).encode("utf-8")
        for room in rooms:
            room_days = []
            for day in days:
                talks = [slot for slot in day["talks"] if slot.room_id == room.pk]
                if talks:
                    room_days.append(day | {"talks": talks})
            files[f"rooms/{room.pk}.html"] = render_to_string(
                "pretalx_static_schedule/room.html",
                context | {"room": room, "days": room_days, "asset_prefix": "../"},
            ).encode("utf-8")
        speakers_by_code = {}
        for code, slots in sessions.items():
            submission = slots[0].submission
            speakers = [
                {
                    "profile": speaker,
                    "answers": public_answers_for_speaker(speaker),
                    "slots": sorted(
                        [
                            slot
                            for other in sessions.values()
                            if speaker in other[0].submission.sorted_speakers
                            for slot in other
                        ],
                        key=lambda slot: slot.start,
                    ),
                }
                for speaker in submission.sorted_speakers
            ]
            for speaker in speakers:
                speaker["submission_count"] = len(
                    {slot.submission_id for slot in speaker["slots"]}
                )
                speakers_by_code.setdefault(speaker["profile"].code, speaker)
            files[f"sessions/{code}.html"] = render_to_string(
                "pretalx_static_schedule/session.html",
                context
                | {
                    "submission": submission,
                    "slots": slots,
                    "speakers": speakers,
                    "recording": recordings[code],
                    "asset_prefix": "../",
                },
            ).encode("utf-8")
        for code, speaker in speakers_by_code.items():
            files[f"speakers/{code}.html"] = render_to_string(
                "pretalx_static_schedule/speaker.html",
                context | {"speaker": speaker, "asset_prefix": "../"},
            ).encode("utf-8")
        for name in [
            "index.html",
            *(day["filename"] for day in days),
            *(f"tracks/{track.pk}.html" for track in tracks),
            *(f"rooms/{room.pk}.html" for room in rooms),
            *(f"sessions/{code}.html" for code in sessions),
            *(f"speakers/{code}.html" for code in speakers_by_code),
        ]:
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

import posixpath

from django.utils.formats import date_format
from django.utils.translation import get_language_info, override

from pretalx.schedule.interfaces.exporters import ScheduleData
from pretalx.submission.domain.queries.question import public_answers_for_speaker

from . import __version__
from .recording import recording_data


def omit_backlink(kind, codes, parent):
    identities = set(codes)
    return len(identities) == 1 and parent == (kind, next(iter(identities)))


def language_name(code):
    try:
        return get_language_info(code)["name"]
    except KeyError:
        return code


class ScheduleRenderData:
    @override("en")
    def __init__(self, event, schedule):
        self.metadata = {
            "title": str(event.name),
            "logo": event.logo,
            "banner": event.header_image,
            "schedule_version": schedule.version,
            "timezone": event.timezone,
            "exporter_version": __version__,
            "source_url": str(event.urls.schedule.full()),
        }
        self.tracks = list(event.tracks.order_by("position", "pk"))
        self.rooms = list(event.rooms.order_by("position", "pk"))
        self.track_names = {track.pk: str(track.name) for track in self.tracks}
        self.room_names = {room.pk: str(room.name) for room in self.rooms}
        self.days = list(ScheduleData(event, schedule=schedule, with_breaks=True).data)
        self.occurrences = {}
        slots_by_code = {}
        for day in self.days:
            day["date"] = day["start"].date().isoformat()
            day["filename"] = f"days/{day['start']:%y-%m-%d}.html"
            day["attributes"] = {"data-day": day["date"]}
            day["talks"] = sorted(
                [slot for room in day["rooms"] for slot in room["talks"]],
                key=lambda slot: (slot.start, slot.pk),
            )
            day["occurrences"] = []
            for slot in day["talks"]:
                submission = slot.submission
                attributes = {
                    "data-kind": "session" if submission else "break",
                    "data-slot": str(slot.pk),
                    "data-day": day["date"],
                    "data-room": str(slot.room_id),
                }
                if submission:
                    attributes["data-session"] = submission.code
                    if submission.track_id:
                        attributes["data-track"] = str(submission.track_id)
                    slots_by_code.setdefault(submission.code, []).append(slot)
                occurrence = {
                    "id": slot.pk,
                    "slot": slot,
                    "attributes": attributes,
                    "title": submission.title if submission else slot.description,
                    "session_code": submission.code if submission else None,
                    "track_id": submission.track_id if submission else None,
                    "room_id": slot.room_id,
                    "room_name": str(slot.room.name),
                    "room_description": str(slot.room.description or ""),
                    "day": day["date"],
                    "start": slot.start.astimezone(event.tz),
                    "end": slot.end.astimezone(event.tz),
                    "duration": slot.duration,
                    "duration_iso": f"PT{slot.duration}M",
                    "signup_status": getattr(slot, "signup_status", None),
                }
                self.occurrences[slot.pk] = occurrence
                day["occurrences"].append(occurrence)
        self.sessions = {}
        self.speakers = {}
        for code, slots in slots_by_code.items():
            submission = slots[0].submission
            profiles = list(submission.sorted_speakers)
            attributes = {"data-kind": "session", "data-session": code}
            if submission.track_id:
                attributes["data-track"] = str(submission.track_id)
            self.sessions[code] = {
                "code": code,
                "filename": f"sessions/{code}.html",
                "submission": submission,
                "title": submission.title,
                "abstract": submission.abstract,
                "description": submission.description,
                "image": submission.image,
                "language": submission.content_locale,
                "language_name": language_name(submission.content_locale),
                "attributes": attributes,
                "slots": slots,
                "occurrences": [self.occurrences[slot.pk] for slot in slots],
                "speaker_codes": [profile.code for profile in profiles],
                "answers": list(submission.public_answers),
                "files": list(submission.public_resources),
                "tags": list(submission.tags.filter(is_public=True)),
                "recording": recording_data(submission),
                "recording_forbidden": submission.do_not_record,
            }
            for profile in profiles:
                if profile.code not in self.speakers:
                    self.speakers[profile.code] = {
                        "code": profile.code,
                        "filename": f"speakers/{profile.code}.html",
                        "profile": profile,
                        "name": profile.get_display_name(),
                        "biography": profile.biography,
                        "image": profile.avatar if event.cfp.request_avatar else None,
                        "language": None,
                        "answers": list(public_answers_for_speaker(profile)),
                        "attributes": {
                            "data-kind": "speaker",
                            "data-speaker": profile.code,
                        },
                        "slots": [],
                        "session_codes": [],
                    }
                speaker = self.speakers[profile.code]
                speaker["slots"].extend(slots)
                speaker["session_codes"].append(code)
        for speaker in self.speakers.values():
            speaker["slots"].sort(key=lambda slot: (slot.start, slot.pk))
            speaker["occurrences"] = [
                self.occurrences[slot.pk] for slot in speaker["slots"]
            ]
            speaker["submission_count"] = len(speaker["session_codes"])
        self.sorted_speakers = sorted(
            self.speakers.values(),
            key=lambda speaker: (speaker["name"].casefold(), speaker["code"]),
        )
        self.navigation = [
            {
                "kind": "sessions",
                "label": "Sessions",
                "items": [
                    {
                        "label": "All sessions",
                        "filename": "index.html",
                        "attributes": {},
                    }
                ],
            },
            {
                "kind": "days",
                "label": "Days",
                "items": [
                    {
                        "label": date_format(day["start"], "F j, Y"),
                        "date": day["date"],
                        "filename": day["filename"],
                        "attributes": day["attributes"],
                    }
                    for day in self.days
                ],
            },
            {
                "kind": "speakers",
                "label": "Speakers",
                "items": [
                    {
                        "label": "All speakers",
                        "filename": "speakers/index.html",
                        "attributes": {},
                    }
                ],
            },
            {
                "kind": "tracks",
                "label": "Tracks",
                "items": [
                    {
                        "label": self.track_names[track.pk],
                        "filename": f"tracks/{track.pk}.html",
                        "attributes": {"data-track": str(track.pk)},
                    }
                    for track in self.tracks
                ],
            },
            {
                "kind": "rooms",
                "label": "Rooms",
                "items": [
                    {
                        "label": self.room_names[room.pk],
                        "filename": f"rooms/{room.pk}.html",
                        "attributes": {"data-room": str(room.pk)},
                    }
                    for room in self.rooms
                ],
            },
        ]

    def session_view(
        self, code, *, occurrence=None, parent=None, expand=True, view="summary"
    ):
        session = self.sessions[code]
        attributes = session["attributes"] | {"data-view": view}
        if occurrence is not None:
            attributes |= occurrence["attributes"]
        speakers = []
        if not omit_backlink("speaker", session["speaker_codes"], parent):
            for speaker_code in session["speaker_codes"]:
                speaker = self.speakers[speaker_code]
                speakers.append(
                    self.speaker_view(
                        speaker_code, parent=("session", code), expand=False
                    )
                    if expand
                    else {
                        "code": speaker_code,
                        "name": speaker["name"],
                        "filename": speaker["filename"],
                        "attributes": speaker["attributes"],
                    }
                )
        return session | {
            "attributes": attributes,
            "occurrences": [occurrence]
            if occurrence is not None
            else session["occurrences"],
            "speakers": speakers,
            "relationship_mode": "summaries" if expand else "references",
        }

    def speaker_view(self, code, *, parent=None, expand=True, view="summary"):
        speaker = self.speakers[code]
        presentations = []
        if not omit_backlink("session", speaker["session_codes"], parent):
            for occurrence in speaker["occurrences"]:
                session_code = occurrence["session_code"]
                session = self.sessions[session_code]
                presentations.append(
                    self.session_view(
                        session_code,
                        occurrence=occurrence,
                        parent=("speaker", code),
                        expand=False,
                    )
                    if expand
                    else {
                        "code": session_code,
                        "title": session["title"],
                        "language": session["language"],
                        "filename": session["filename"],
                        "occurrence": occurrence,
                        "attributes": occurrence["attributes"],
                    }
                )
        return speaker | {
            "attributes": speaker["attributes"] | {"data-view": view},
            "presentations": presentations,
            "relationship_mode": "summaries" if expand else "references",
        }

    def filtered_days(self, field, value):
        days = []
        for day in self.days:
            occurrences = [item for item in day["occurrences"] if item[field] == value]
            if occurrences:
                days.append(
                    day
                    | {
                        "occurrences": occurrences,
                        "talks": [item["slot"] for item in occurrences],
                    }
                )
        return days

    def page(self, kind, filename, title):
        return {
            "kind": kind,
            "filename": filename,
            "title": title,
            "attributes": {"data-page": kind},
            "asset_prefix": "../" if posixpath.dirname(filename) else "",
            "navigation": [
                group
                | {
                    "items": [
                        item | {"is_current": item["filename"] == filename}
                        for item in group["items"]
                    ]
                }
                for group in self.navigation
            ],
        }

    def overview(self, kind, filename, title, days):
        groups = []
        for day in days:
            items = []
            for occurrence in day["occurrences"]:
                code = occurrence["session_code"]
                items.append(
                    self.session_view(code, occurrence=occurrence)
                    if code
                    else occurrence
                )
            groups.append(
                {"date": day["date"], "attributes": day["attributes"], "items": items}
            )
        return self.page(kind, filename, title) | {"days": days, "groups": groups}

    def pages(self):
        yield self.overview("sessions", "index.html", "All sessions", self.days) | {
            "template": "schedule.html",
            "is_overview": True,
        }
        for day in self.days:
            yield self.overview("day", day["filename"], day["date"], [day]) | {
                "template": "schedule.html",
                "overview_day": day,
                "attributes": {"data-page": "day"} | day["attributes"],
            }
        for track in self.tracks:
            yield self.overview(
                "track",
                f"tracks/{track.pk}.html",
                self.track_names[track.pk],
                self.filtered_days("track_id", track.pk),
            ) | {
                "template": "track.html",
                "track": track,
                "attributes": {"data-page": "track", "data-track": str(track.pk)},
            }
        for room in self.rooms:
            yield self.overview(
                "room",
                f"rooms/{room.pk}.html",
                self.room_names[room.pk],
                self.filtered_days("room_id", room.pk),
            ) | {
                "template": "room.html",
                "room": room,
                "attributes": {"data-page": "room", "data-room": str(room.pk)},
            }
        for code, session in self.sessions.items():
            item = self.session_view(code, view="detail")
            yield self.page("session", session["filename"], session["title"]) | {
                "template": "session.html",
                "item": item,
                "session": item,
                "submission": session["submission"],
                "slots": session["slots"],
                "speakers": item["speakers"],
                "recording": session["recording"],
            }
        for code, speaker in self.speakers.items():
            item = self.speaker_view(code, view="detail")
            yield self.page("speaker", speaker["filename"], speaker["name"]) | {
                "template": "speaker.html",
                "item": item,
                "speaker": item,
            }
        yield self.page("speakers", "speakers/index.html", "All speakers") | {
            "template": "speaker_overview.html",
            "items": [
                self.speaker_view(speaker["code"]) for speaker in self.sorted_speakers
            ],
            "is_speaker_overview": True,
        }

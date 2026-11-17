import datetime as dt
from io import BytesIO

import pytest
from django.core import management
from django.core.files.uploadedfile import SimpleUploadedFile
from django_scopes import scope, scopes_disabled
from PIL import Image

from pretalx.event.domain.event import initialise_event
from pretalx.event.domain.plugins import enable_plugin
from pretalx.event.models import Event, Organiser, Team
from pretalx.person.models import ProfilePicture, SpeakerProfile, User
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.schedule.models import Room, TalkSlot
from pretalx.submission.models import Submission, SubmissionStates, Track


@pytest.fixture(scope="session", autouse=True)
def collect_static(request):
    management.call_command("collectstatic", "--noinput", "--clear")


@pytest.fixture
def organiser():
    with scopes_disabled():
        o = Organiser.objects.create(name="Super Organiser", slug="superorganiser")
        Team.objects.create(
            name="Organisers",
            organiser=o,
            can_create_events=True,
            can_change_teams=True,
            can_change_organiser_settings=True,
            can_change_event_settings=True,
            can_change_submissions=True,
        )
        Team.objects.create(name="Reviewers", organiser=o, is_reviewer=True)
    return o


@pytest.fixture
def register_signal_handler(settings):
    registered = []
    settings.CORE_MODULES = [*settings.CORE_MODULES, "tests._test_plugin"]

    def register(signal, handler):
        handler.__module__ = "tests._test_plugin"
        signal.connect(handler)
        registered.append((signal, handler))

    yield register

    for signal, handler in registered:
        signal.disconnect(handler)


@pytest.fixture
def event(organiser):
    today = dt.date.today()
    with scopes_disabled():
        event = Event.objects.create(
            name="Fancy testevent",
            is_public=True,
            slug="test",
            email="orga@orga.org",
            timezone="Europe/Berlin",
            date_from=today,
            date_to=today + dt.timedelta(days=3),
            organiser=organiser,
        )
        initialise_event(event)
        enable_plugin(event, "pretalx_static_schedule")
        event.save()
        for team in organiser.teams.all():
            team.limit_events.add(event)
    return event


@pytest.fixture
def published_schedule(event):
    with scopes_disabled():
        schedule, _ = freeze_schedule(event.wip_schedule, "1.0", notify_speakers=False)
    return schedule


@pytest.fixture
def published_talk_slot(event):
    with scopes_disabled():
        speaker = SpeakerProfile.objects.create(
            event=event,
            code="ADA0001",
            name="Ada Example",
            biography="Builds **small tools**.",
        )
        submission = Submission.objects.create(
            event=event,
            code="TOOLS01",
            title="Building <tools>",
            submission_type=event.submission_types.first(),
            track=Track.objects.create(event=event, name="Tools"),
            state=SubmissionStates.CONFIRMED,
            abstract="A **small** tool with a [project](https://example.org).",
            description="A longer description of the tool.",
            notes="Private organiser notes",
            duration=30,
        )
        submission.speakers.add(speaker)
        start = dt.datetime.combine(event.date_from, dt.time(10), tzinfo=event.tz)
        slot = TalkSlot.objects.create(
            schedule=event.wip_schedule,
            submission=submission,
            room=Room.objects.create(
                event=event, name="Room A", description="Main hall"
            ),
            start=start,
            end=start + dt.timedelta(minutes=45),
        )
        freeze_schedule(event.wip_schedule, "1.0", notify_speakers=False)
    return slot


@pytest.fixture
def published_speaker(published_talk_slot, event):
    with scope(event=event):
        return published_talk_slot.submission.sorted_speakers.first()


@pytest.fixture
def image_uploads(published_talk_slot, event):
    data = BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, "PNG")
    contents = data.getvalue()
    with scope(event=event):
        submission = published_talk_slot.submission
        event.logo.save("logo.png", SimpleUploadedFile("logo.png", contents))
        event.header_image.save(
            "header.png", SimpleUploadedFile("header.png", contents)
        )
        submission.image.save(
            "session.png", SimpleUploadedFile("session.png", contents)
        )
        user = User.objects.create_user(email="speaker@example.org", name="Ada Example")
        picture = ProfilePicture.objects.create(
            user=user, avatar=SimpleUploadedFile("avatar.png", contents)
        )
        speaker = submission.sorted_speakers.first()
        speaker.profile_picture = picture
        speaker.save(update_fields=["profile_picture"])
    return {
        "logo": event.logo,
        "header": event.header_image,
        "session": submission.image,
        "avatar": picture.avatar,
    }, contents


@pytest.fixture
def orga_user(event):
    with scopes_disabled():
        user = User.objects.create_user(
            password="orgapassw0rd", email="orgauser@orga.org", name="Orga User"
        )
        team = event.organiser.teams.filter(
            can_change_organiser_settings=True, is_reviewer=False
        ).first()
        team.members.add(user)
        team.save()
    return user


@pytest.fixture
def review_user(event):
    with scopes_disabled():
        user = User.objects.create_user(
            password="reviewpassw0rd", email="reviewuser@orga.org", name="Review User"
        )
        team = event.organiser.teams.filter(
            can_change_organiser_settings=False, is_reviewer=True
        ).first()
        team.members.add(user)
        team.save()
    return user


@pytest.fixture
def orga_client(orga_user, client):
    client.force_login(orga_user)
    return client


@pytest.fixture
def review_client(review_user, client):
    client.force_login(review_user)
    return client

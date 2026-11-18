pretalx-static-schedule
=======================

Exports the latest published pretalx schedule as static HTML pages in one ZIP
archive. The pages work without JavaScript or a running pretalx server.
Generated interface text and date formatting use English. Speaker and
submission content is rendered in its supplied language.

Installation
------------

Clone this repository. Activate the Python environment that runs pretalx
2026.2.1. In the repository directory, run::

    uv pip install .

Restart pretalx.

Export
------

Log in as an organiser. Enable the plugin under **Settings → Plugins**.
Publish a schedule. Open **Schedule → Export → More exports** and select
**Static HTML (ZIP)**. The export uses the latest published schedule.

ZIP contents
------------

* ``index.html``: schedule overview.
* ``days/<yy-mm-dd>.html``: one overview for each schedule day.
* ``tracks/<id>.html``: one page for each track.
* ``rooms/<id>.html``: one overview for each room.
* ``sessions/<code>.html``: session details and public speaker information.
* ``speakers/index.html``: alphabetical overview of speakers.
* ``speakers/<code>.html``: speaker pages.
* ``assets/schedule.css``: stylesheet.
* ``assets/variables.css``: the original pretalx CSS variables.
* ``media/``: uploaded images and public files, when present.

Speaker pages include the biography, public photo, public answers and files,
and the speaker's sessions in the exported schedule.
The speaker overview lists each speaker in the current published schedule
once, sorted by their full displayed name without regard to case. Each name
links to the speaker's page. Speakers with identical names have separate
entries. Each entry uses the same speaker summary as the session pages,
including the public photo, biography, answers and files. The speaker overview
lists all of each speaker's published sessions, including speakers with just
one session. On session pages, the speaker's session list appears only when
they have more than one distinct session in the published schedule. Repeated
occurrences of the same session count as one session.
Schedules without speakers show an empty overview.

Every page has lists of days, tracks and rooms above its session content.
The current overview is highlighted; the other entries link to their pages.
The day list includes an "All days" link to the full overview. Lists include
empty days, tracks and rooms.
An "All speakers" link appears alongside these lists on every page and is
highlighted on the speaker overview.

Day headings on the full overview link to the daily pages. Daily pages include
empty days and link back to the full overview.

Track pages include the track description and its published sessions, grouped
by schedule day. Tracks without sessions have an empty page. Track names on
session and speaker pages link to their track pages. Track filenames use IDs,
so renaming a track does not change its URL.

Room pages include the attendee description and published sessions and breaks,
grouped by schedule day. Empty rooms have an empty page. Rooms appear in their
configured order. Room names on session cards and detail pages link to their
room overview. Room filenames use IDs, so renaming a room does not change its URL.

Session pages show the content language and assigned track, even when these
fields are hidden on the public pages.

Pages use the event's primary color. Session cards and track labels use the
assigned track color. Public tags appear as badges with their assigned colors.

Session pages include recording information from enabled recording providers.
Recording descriptions and labels are kept as text. Links in provider HTML,
including iframe and media URLs, become ordinary links to the original site.

Session pages also include public notes and content from enabled plugins above
and below the session details. Text, basic formatting, tables and links are
kept. Interactive widgets and online actions are removed. Local images are
copied into the archive. Remote images become links to their original location.

Extract the ZIP into a web server directory. Keep its directory structure.
For example, if the server's document root is ``/var/www/html/``::

    unzip downloaded-schedule.zip -d /var/www/html/conference/

Replace ``downloaded-schedule.zip`` with the downloaded filename. Open
``/conference/index.html`` on that server. The files also work at the server
root or under a different domain.

HTML templates
--------------

Use standard Django template overrides. Place files in
``templates/pretalx_static_schedule/`` inside the configured pretalx data directory.
These paths override the default templates:

* ``pretalx_static_schedule/base.html``: shared HTML layout.
* ``pretalx_static_schedule/navigation.html``: shared day, track and room navigation.
* ``pretalx_static_schedule/schedule.html``: full and daily schedule overviews.
* ``pretalx_static_schedule/session.html``: session details.
* ``pretalx_static_schedule/speaker.html``: speaker pages.
* ``pretalx_static_schedule/speaker_overview.html``: alphabetical speaker overview.
* ``pretalx_static_schedule/track.html``: track pages.
* ``pretalx_static_schedule/room.html``: room overviews.
* ``pretalx_static_schedule/speaker_info.html``: shared speaker photo, biography and answers.
* ``pretalx_static_schedule/speaker_summary.html``: shared speaker summary and optional session links on session pages and the speaker overview.
* ``pretalx_static_schedule/session_summary.html``: session cards on overview, track, room and speaker pages.
* ``pretalx_static_schedule/tags.html``: public tag badges.
* ``pretalx_static_schedule/answers.html``: public session and speaker answers.
* ``pretalx_static_schedule/recording.html``: recording information and links.

The page templates extend ``base.html``. They use the ``title``, ``header``,
``navigation`` and ``content`` blocks. The ``navigation`` block includes the
shared navigation template. For example, add this to your override of ``schedule.html``::

    {% extends "pretalx_static_schedule/schedule.html" %}
    {% block content %}
        {{ block.super }}
        <p>Welcome to {{ event.name }}.</p>
    {% endblock %}

Templates receive ``metadata`` with event and export information and ``page``
with the current view's prepared data. Page data includes semantic attributes,
navigation destinations, grouped session occurrences or speaker entries, and
related summaries or references. Relationship lists omit a sole reference
back to their parent entity; top-level lists include every published entry.

The ``speaker_summary.html`` template is shared by session pages and the
speaker overview. It includes ``speaker_info.html``, which is also used on
individual speaker pages.
The overview includes ``speaker_summary.html`` with ``show_all_sessions=True``
to show the session list even for speakers with only one session.
The ``session_summary.html`` template uses ``session_prefix`` for relative
session links. Use ``{{ session_prefix }}{{ slot.submission.code }}.html`` in
custom session cards.

Keep links to exported pages and assets relative. Restart pretalx after changing
templates. Django caches templates in production.

Speaker names on the schedule overview link to ``speakers/<code>.html``.

The stylesheet is exported as ``assets/schedule.css``. Uploaded images and
public files are copied into ``media/``. In an override, use the ``local_asset``
template tag to copy an uploaded file and get its relative URL::

    {% load static_schedule %}
    {% local_asset submission.image as image_url %}
    {% if image_url %}<img src="{{ image_url }}" alt="Session image">{% endif %}

Use ``{{ asset_prefix }}assets/schedule.css`` to link the included stylesheet.

The recording template receives ``recording.text`` and ``recording.links``.
Each link has ``url`` and ``label`` fields. Recording URLs point to their
original location outside the archive.

Custom session templates can include plugin content with these tags::

    {% load static_schedule %}
    {% session_content "pretalx.agenda.signals.html_above_session_pages" %}
    {% session_content "pretalx.agenda.signals.html_below_session_pages" %}

Development checks
------------------

Use Python 3.13 or later, uv and just. CI uses Python 3.14.
Install the locked dependencies::

    uv sync --frozen --extra dev

Run the tests with 100% line and branch coverage::

    just test

Run formatting and lint checks::

    just fmt-check

Apply formatting::

    just fmt

Run the same checks as CI, including package checks::

    just ci

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
* ``assets/variables.css``: pretalx CSS variables with event, track and public-tag color rules.
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

Every page has a shared header with the event logo and title, published
schedule version and timezone. Header navigation links to all sessions, every
day, all speakers, every track and every room, in that order. It includes empty
days, tracks and rooms; empty track and room groups are omitted. The current
page's link is highlighted and remains usable. A footer identifies the exporter
version and links to the original schedule.

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

Every session summary shows the title, image when present, abstract, scheduled
date and times, actual duration, room, content language, assigned track and
public tags. The same component is used on schedule, day, track, room and
speaker pages. Session details add the full description, every scheduled
occurrence, public answers, files, recordings and plugin content.

Overview entries use compact links for related speakers and presentations.
Session detail pages embed speaker summaries; speaker detail pages embed
session summaries. Those embedded summaries also use compact relationship links.
A relationship list is omitted only when its sole distinct entity is the
surrounding parent. Repeated occurrences of one session still count as one
entity. Root overview and detail lists include single entries.

Pages use the event's primary color. Session cards and track labels use the
assigned track color. Public tags appear as badges with their assigned colors.
Navigation and tag text use a contrasting foreground; links use darker shades
of the event or track color. Invalid track and tag colors use the event color;
an invalid event color uses pretalx's default.

All layout, colors and visible interface captions are supplied by the external
stylesheets. Default templates, rendered rich text and plugin fragments contain
no inline styles or presentation attributes. English accessible names remain
in the HTML. Session and speaker summaries use the same typography, spacing
and field styling on every page, independent of heading depth or surrounding
entity. The stylesheet supports narrow screens and print; printing omits the
navigation and banner while retaining the event identity and schedule metadata.

Session pages include recording information from enabled recording providers.
Recording descriptions and labels are kept as text. Links in provider HTML,
including iframe and media URLs, become ordinary links to the original site.

Session pages also include public notes and content from enabled plugins above
and below the session details. Text, basic formatting, tables and links are
kept. Interactive widgets and online actions are removed. Local images are
copied into the archive. Remote images become links to their original location.
Presentation classes, table widths and alignment are removed. Literal HTML
and CSS examples in escaped prose or code remain content.
Authored headings follow their containing article's heading depth, with skipped
ranks compressed and a maximum rank of ``h6``. Plugin anchor IDs are unique
within each document. Links to anchors in the same fragment are rewritten;
anchors outside that fragment continue to point to the source session page.

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
* ``pretalx_static_schedule/navigation.html``: shared navigation for all pages.
* ``pretalx_static_schedule/event_variables.css``: event, track and public-tag colors appended to the exported variables stylesheet.
* ``pretalx_static_schedule/schedule.html``: full and daily schedule overviews.
* ``pretalx_static_schedule/session.html``: session details.
* ``pretalx_static_schedule/speaker.html``: speaker pages.
* ``pretalx_static_schedule/speaker_overview.html``: alphabetical speaker overview.
* ``pretalx_static_schedule/track.html``: track pages.
* ``pretalx_static_schedule/room.html``: room overviews.
* ``pretalx_static_schedule/speaker_info.html``: shared speaker photo, biography and answers.
* ``pretalx_static_schedule/speaker_article.html``: canonical speaker summary and detail structure.
* ``pretalx_static_schedule/session_article.html``: canonical session summary and detail structure.
* ``pretalx_static_schedule/speaker_summary.html``: shared speaker summaries in every context.
* ``pretalx_static_schedule/session_summary.html``: session cards on overview, track, room and speaker pages.
* ``pretalx_static_schedule/session_groups.html``: schedule-day groups and chronological session lists.
* ``pretalx_static_schedule/occurrence.html``: scheduled times, duration, room and signup state.
* ``pretalx_static_schedule/break.html``: scheduled breaks.
* ``pretalx_static_schedule/tags.html``: public tag badges.
* ``pretalx_static_schedule/answers.html``: public session and speaker answers.
* ``pretalx_static_schedule/recording.html``: recording information and links.

The page templates extend ``base.html``. It provides the ``title``, ``header``,
``navigation``, ``content`` and ``footer`` blocks. The header and footer have
shared defaults; the ``navigation`` block includes the shared navigation
template inside the header. The ``title`` block defaults to the page title and
event name. For example, add this to your override of ``schedule.html``::

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
Navigation uses ``page.navigation``; the previous ``navigation_days`` variable
is no longer supplied. The shared shell uses semantic ``data-field``,
``data-group`` and ``data-page`` attributes. Its visible captions come from
CSS, with English accessible names in HTML.

Summary templates receive a prepared ``speaker`` or ``session`` record and
``heading_level`` for the current document depth. Their article templates also
serve detail pages, using ``view`` to select the additional fields. Relationships
are already prepared as ``speaker.presentations`` or ``session.speakers``;
templates do not count entities or expand them further.
The previous ``show_all_sessions``, ``show_date`` and ``session_prefix`` options
are no longer used. In custom session summaries, use ``session.title`` and
``session.url``. Internal pretalx URLs are rewritten to relative archive paths.
Session prose carries its known language; speaker biography language is not
inferred from the sessions.

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

Override ``pretalx_static_schedule/schedule.css`` through Django's static file
finders to replace the stylesheet. Semantic ``data-kind`` and ``data-view``
attributes identify summaries and details; ``data-day``, ``data-room`` and
``data-track`` identify occurrences and navigation destinations. Relationship
lists use ``data-rel``. You can style a summary by its own attributes, its
position in a list or its surrounding entity. For example::

    article[data-kind="session"][data-view="summary"][data-room="1"] {
        border-inline-end: 0.3rem solid navy;
    }
    article[data-kind="session"][data-view="detail"] > [data-rel="speakers"] > li > article {
        background-color: aliceblue;
    }

These context selectors are available for custom styling; the defaults give
summaries the same appearance everywhere. Generated event CSS defines
``--track-color`` on ``[data-track="<id>"]`` and ``--tag-color`` and
``--tag-text-color`` on public ``[data-tag="<id>"]`` elements. Visible English
captions use ``::before`` on semantic fields, navigation groups and relationship
lists. Render authored prose in custom templates with the ``schedule_text``
filter from ``static_schedule`` to preserve formatting while removing
presentation attributes.

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

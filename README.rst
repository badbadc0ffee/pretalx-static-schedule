pretalx-static-schedule
=======================

Exports the latest published pretalx schedule as static HTML pages in one ZIP
archive. The pages work without JavaScript or a running pretalx server.

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
* ``sessions/<code>.html``: session details and public speaker information.
* ``speakers/<code>.html``: speaker pages.
* ``assets/schedule.css``: stylesheet.
* ``media/``: uploaded images and public files, when present.

Session pages show the content language and assigned track, even when these
fields are hidden on the public pages.

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
* ``pretalx_static_schedule/schedule.html``: schedule overview.
* ``pretalx_static_schedule/session.html``: session details.
* ``pretalx_static_schedule/speaker.html``: speaker pages.
* ``pretalx_static_schedule/answers.html``: public session and speaker answers.
* ``pretalx_static_schedule/recording.html``: recording information and links.

The page templates extend ``base.html``. They use the ``title``, ``header`` and
``content`` blocks. For example, add this to your override of ``schedule.html``::

    {% extends "pretalx_static_schedule/schedule.html" %}
    {% block content %}
        {{ block.super }}
        <p>Welcome to {{ event.name }}.</p>
    {% endblock %}

Keep links to exported pages and assets relative. Restart pretalx after changing
templates. Django caches templates in production.

Keep the ``speaker-{{ speaker.profile.code }}`` section IDs in custom session
templates. Links to speaker profiles point to these sections.

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

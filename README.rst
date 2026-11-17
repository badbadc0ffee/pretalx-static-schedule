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
* ``assets/schedule.css``: stylesheet.
* ``media/``: uploaded images and public files, when present.

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
* ``pretalx_static_schedule/answers.html``: public session and speaker answers.

Both page templates extend ``base.html``. They use the ``title``, ``header`` and
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

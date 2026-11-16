pretalx-static-schedule
=======================

A plugin project for exporting the current published pretalx schedule as static
HTML pages.

Installation
------------

Install in the same Python environment as pretalx 2026.2.1::

    uv pip install -e .

HTML templates
--------------

Use standard Django template overrides. Place files in
``templates/pretalx_static_schedule/`` inside the configured pretalx data directory.
These paths override the default templates:

* ``pretalx_static_schedule/base.html``: shared HTML layout.
* ``pretalx_static_schedule/schedule.html``: schedule overview.
* ``pretalx_static_schedule/session.html``: session details.

Both page templates extend ``base.html``. They use the ``title``, ``header`` and
``content`` blocks. For example, add this to your override of ``schedule.html``::

    {% extends "pretalx_static_schedule/schedule.html" %}
    {% block content %}
        {{ block.super }}
        <p>Welcome to {{ event.name }}.</p>
    {% endblock %}

Keep links to exported pages and assets relative. Restart pretalx after changing
templates. Django caches templates in production.

Development checks
------------------

Use Python 3.13 or later, uv and just. CI uses Python 3.14.
Install the locked dependencies::

    uv sync --frozen --extra dev

Tests use table synchronization, as pretalx's local test settings do.
Run the tests with 100% line and branch coverage::

    just test

Run formatting and lint checks::

    just fmt-check

Apply formatting::

    just fmt

Run the same checks as CI, including package checks::

    just ci

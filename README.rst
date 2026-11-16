pretalx-static-schedule
=======================

A plugin project for exporting the current published pretalx schedule as static
HTML pages.

Installation
------------

Install in the same Python environment as pretalx 2026.2.1::

    uv pip install -e .

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

The generated Cookiecutter settings example is retained for bootstrap checks.
It will be removed when plugin registration is implemented.

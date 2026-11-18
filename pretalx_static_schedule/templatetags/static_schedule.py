import re
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

from bleach import clean
from bs4 import BeautifulSoup
from django import template
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.core.files.storage import default_storage
from django.core.files.utils import validate_file_name
from django.core.handlers.wsgi import WSGIRequest
from django.core.signing import BadSignature, Signer
from django.urls import resolve, reverse
from django.utils.safestring import mark_safe
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from pretalx.common.templatetags.html_signal import html_signal
from pretalx.common.templatetags.rich_text import (
    ALLOWED_PROTOCOLS,
    ALLOWED_TAGS,
    render_markdown_abslinks,
)
from pretalx.common.ui import has_good_contrast
from pretalx.common.urls import get_base_url

register = template.Library()


@register.filter
def css_color(value, fallback="var(--color-primary)"):
    return (
        value
        if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value or "")
        else fallback
    )


@register.filter
def css_text_color(value, fallback="#3aa57c"):
    color = css_color(value, css_color(fallback, "#3aa57c")).lstrip("#")
    if len(color) == 3:
        color = "".join(channel * 2 for channel in color)
    return (
        "var(--color-white)" if has_good_contrast(f"#{color}") else "var(--color-text)"
    )


def normalize_headings(soup, heading_level):
    hierarchy = []
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        level = int(heading.name[1])
        while hierarchy and hierarchy[-1] >= level:
            hierarchy.pop()
        hierarchy.append(level)
        heading.name = f"h{min(6, int(heading_level) + len(hierarchy))}"


@register.filter
def schedule_text(value, heading_level=1):
    soup = BeautifulSoup(
        clean(
            render_markdown_abslinks(value),
            tags=ALLOWED_TAGS,
            attributes={
                "a": ["href", "title", "rel", "target"],
                "abbr": ["title"],
                "acronym": ["title"],
            },
            protocols=ALLOWED_PROTOCOLS,
            strip=True,
        ),
        "html.parser",
    )
    normalize_headings(soup, heading_level)
    return mark_safe(str(soup))  # noqa: S308 -- bleach removes active HTML and presentation attributes.


@register.simple_tag(takes_context=True)
def local_asset(context, file):
    if not file:
        return ""
    url = urljoin(get_base_url(context["event"]), file.url)
    if (
        urlsplit(url)._replace(query="", fragment="").geturl()
        in context["recording_urls"]
    ):
        return url
    path = f"media/{file.name}"
    with file.open("rb") as source:
        context["files"][path] = source.read()
    destination = quote(path)
    context["links"][file.url] = destination
    context["links"][url] = destination
    return context["asset_prefix"] + destination


@register.filter
def public_tags(submission):
    return submission.tags.filter(is_public=True)


@register.simple_tag(takes_context=True)
def session_content(context, signal_name):
    submission = context["submission"]
    url = urlsplit(submission.urls.public.full())
    request = WSGIRequest(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": url.path,
            "HTTP_HOST": url.netloc,
            "wsgi.url_scheme": url.scheme,
            "wsgi.input": BytesIO(),
        }
    )
    request.event = context["event"]
    request.user = AnonymousUser()
    request.session = {}
    request.LANGUAGE_CODE = get_language()
    request.resolver_match = resolve(request.path_info)
    request.timezone = request.event.timezone
    request.uses_custom_domain = bool(request.event.custom_domain)
    request.host = url.hostname
    request.port = url.port
    soup = BeautifulSoup(
        html_signal(
            signal_name, sender=request.event, request=request, submission=submission
        ),
        "html.parser",
    )
    for element in soup.select(
        "script, style, form, button, input, select, textarea, iframe, object, embed, [hidden]"
    ):
        element.decompose()
    normalize_headings(soup, context.get("heading_level", 1))
    position = (
        signal_name.rsplit(".", 1)[-1]
        .removeprefix("html_")
        .removesuffix("_session_pages")
    )
    anchors = {}
    for index, element in enumerate(soup.select("[id]"), start=1):
        identity = f"{submission.code}-{position}-{index}"
        anchors.setdefault(element["id"], identity)
        element["id"] = identity
    for link in soup.select('a[href^="#"]'):
        target = unquote(link["href"][1:])
        if target in anchors:
            link["href"] = f"#{anchors[target]}"
        else:
            link["href"] = urljoin(submission.urls.public.full(), link["href"])
    media_url = urlsplit(urljoin(get_base_url(request.event), default_storage.url("")))
    static_url = urlsplit(urljoin(get_base_url(request.event), settings.STATIC_URL))
    for image in soup.select("img[src]"):
        source_url = urljoin(submission.urls.public.full(), image["src"])
        image_url = urlsplit(source_url)
        if image_url.netloc == media_url.netloc and image_url.path.startswith(
            media_url.path
        ):
            name = unquote(image_url.path.removeprefix(media_url.path))
            validate_file_name(name, allow_relative_path=True)
            path = f"media/{name}"
            with default_storage.open(name, "rb") as source:
                context["files"][path] = source.read()
        elif image_url.netloc == static_url.netloc and image_url.path.startswith(
            static_url.path
        ):
            name = unquote(image_url.path.removeprefix(static_url.path))
            validate_file_name(name, allow_relative_path=True)
            path = f"assets/{name}"
            context["files"][path] = Path(finders.find(name)).read_bytes()
        else:
            link = soup.new_tag("a", href=source_url)
            link.string = str(image.get("alt") or _("Image"))
            image.replace_with(link)
            continue
        destination = quote(path)
        context["links"][image["src"]] = destination
        context["links"][source_url] = destination
        image["src"] = context["asset_prefix"] + destination
    online_urls = {
        target.full()
        for target in (
            submission.urls.feedback,
            submission.urls.user_base,
            submission.urls.signup,
            submission.urls.signup_cancel,
            submission.urls.ical,
            request.event.urls.login,
        )
    }
    for link in reversed(soup.select("a[href]")):
        destination = urljoin(submission.urls.public.full(), link["href"])
        parts = urlsplit(destination)
        if parts.netloc == url.netloc and parts.path == reverse("redirect"):
            try:
                destination = Signer(salt="safe-redirect").unsign(
                    parse_qs(parts.query).get("url", [""])[0]
                )
            except BadSignature:
                link.unwrap()
                continue
            parts = urlsplit(destination)
        if (
            parts.scheme not in ALLOWED_PROTOCOLS | {""}
            or urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            in online_urls
        ):
            link.decompose()
            continue
        if not link["href"].startswith("#"):
            link["href"] = destination
    return mark_safe(  # noqa: S308 -- bleach removes active HTML.
        clean(
            str(soup),
            tags=ALLOWED_TAGS | {"section", "dl", "dt", "dd", "img"},
            attributes={
                "*": ["id"],
                "a": ["href", "title"],
                "abbr": ["title"],
                "acronym": ["title"],
                "img": ["src", "alt", "title"],
            },
            protocols=ALLOWED_PROTOCOLS,
            strip=True,
        )
    )

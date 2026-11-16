from urllib.parse import quote, urljoin

from django import template

from pretalx.common.urls import get_base_url

register = template.Library()


@register.simple_tag(takes_context=True)
def local_asset(context, file):
    if not file:
        return ""
    path = f"media/{file.name}"
    with file.open("rb") as source:
        context["files"][path] = source.read()
    destination = quote(path)
    context["links"][file.url] = destination
    context["links"][urljoin(get_base_url(context["event"]), file.url)] = destination
    return context["asset_prefix"] + destination

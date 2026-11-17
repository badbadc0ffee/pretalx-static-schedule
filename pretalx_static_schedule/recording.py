from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from django.utils.translation import gettext_lazy as _

from pretalx.agenda.recording import get_recording


def recording_data(submission):
    html = get_recording(submission).get("iframe", "")
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script, style"):
        element.decompose()
    links = []
    for element in soup.select(
        "iframe[src], a[href], video[src], audio[src], source[src]"
    ):
        url = element.get("href", element.get("src")).strip()
        if not url or url.startswith("#"):
            continue
        url = urljoin(submission.urls.public.full(), url)
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            continue
        links.append(
            {
                "url": url,
                "label": element.get("title")
                or element.get_text(" ", strip=True)
                or element.get("type")
                or _("Recording"),
            }
        )
    return {"text": soup.get_text(" ", strip=True), "links": links}

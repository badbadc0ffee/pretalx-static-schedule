import pytest
from bs4 import BeautifulSoup
from django.template.loader import render_to_string
from django_scopes import scope
from pytest_django.asserts import assertInHTML

from pretalx.submission.models import Answer, AnswerOption, Question

from pretalx_static_schedule.exporter import StaticScheduleExporter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("target", ("submission", "speaker"))
@pytest.mark.parametrize(
    ("variant", "value"),
    (
        ("number", "0"),
        ("string", "A **short** answer."),
        ("text", "First **line**.\n\nSecond line."),
        ("date", "2026-11-15"),
        ("datetime", "2026-11-15T09:30"),
        ("date", ""),
        ("datetime", ""),
    ),
)
def test_scalar_answers_match_online_page(
    published_talk_slot, event, variant, value, target
):
    with scope(event=event):
        obj = published_talk_slot.submission
        if target == "speaker":
            obj = obj.sorted_speakers.first()
        question = Question.objects.create(
            event=event,
            question=f"Public {variant}",
            variant=variant,
            target=target,
            is_public=True,
        )
        answer = Answer.objects.create(question=question, answer=value, **{target: obj})
        online_answer = render_to_string(
            "common/question_answer.html", {"answer": answer}
        )
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assertInHTML(f"<dt>{question.question}</dt>", html)
    if value:
        assertInHTML(online_answer, html)
    else:
        assert (
            BeautifulSoup(html, "html.parser").select_one('[data-state="no-response"]')[
                "aria-label"
            ]
            == "No response"
        )


@pytest.mark.parametrize("target", ("submission", "speaker"))
@pytest.mark.parametrize(
    ("variant", "labels"),
    (
        ("choices", ("*Workshop*",)),
        ("multiple_choice", ("*Workshop*", "Q&A <session>")),
    ),
)
def test_choice_labels_match_online_page(
    published_talk_slot, event, variant, labels, target
):
    with scope(event=event):
        obj = published_talk_slot.submission
        if target == "speaker":
            obj = obj.sorted_speakers.first()
        question = Question.objects.create(
            event=event,
            question="Format",
            variant=variant,
            target=target,
            is_public=True,
        )
        answer = Answer.objects.create(question=question, answer="", **{target: obj})
        for label in labels:
            answer.options.add(
                AnswerOption.objects.create(question=question, answer=label)
            )
        online_answer = render_to_string(
            "common/question_answer.html", {"answer": answer}
        )
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assertInHTML("<dt>Format</dt>", html)
    assertInHTML(f"<dd>{online_answer}</dd>", html)


@pytest.mark.parametrize("target", ("submission", "speaker"))
@pytest.mark.parametrize("variant", ("boolean", "choices", "multiple_choice"))
def test_unanswered_typed_field(published_talk_slot, event, variant, target):
    with scope(event=event):
        obj = published_talk_slot.submission
        if target == "speaker":
            obj = obj.sorted_speakers.first()
        question = Question.objects.create(
            event=event,
            question=f"Public {variant}",
            variant=variant,
            target=target,
            is_public=True,
        )
        Answer.objects.create(question=question, answer="", **{target: obj})
        html = (
            StaticScheduleExporter(event)
            .get_files()["sessions/TOOLS01.html"]
            .decode("utf-8")
        )

    assertInHTML(f"<dt>{question.question}</dt>", html)
    assert (
        BeautifulSoup(html, "html.parser").select_one('[data-state="no-response"]')[
            "aria-label"
        ]
        == "No response"
    )

from i18nfield.forms import I18nModelForm

from .models import StaticScheduleSettings


class StaticScheduleSettingsForm(I18nModelForm):
    def __init__(self, *args, event=None, **kwargs):
        self.instance, _ = StaticScheduleSettings.objects.get_or_create(event=event)
        super().__init__(*args, **kwargs, instance=self.instance, locales=event.locales)

    class Meta:
        model = StaticScheduleSettings
        fields = ("some_setting",)
        widgets = {}

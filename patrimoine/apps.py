from django.apps import AppConfig


class PatrimoineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "patrimoine"
    verbose_name = "Patrimoine"

    def ready(self):
        from . import base_essai

        base_essai.brancher_signaux()

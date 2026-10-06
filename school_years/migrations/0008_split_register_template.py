"""Один шаблон журнала становится двумя: для 0-3 и для 4-11.

Половины школы оценивают по разным шкалам, и бланк журнала у них разный. Пока поле было одно,
одной из половин его правили руками после копирования.

Старое значение переносится в оба новых поля, а не пропадает: какой именно половине оно
подходило, знает школа, и ей проще заменить одну ссылку, чем вспоминать обе.
"""

from django.db import migrations, models


def carry_over(apps, schema_editor):
    SchoolYear = apps.get_model("school_years", "SchoolYear")
    for year in SchoolYear.objects.exclude(class_register_template_url=""):
        year.class_register_template_0_3_url = year.class_register_template_url
        year.class_register_template_4_11_url = year.class_register_template_url
        year.save(
            update_fields=[
                "class_register_template_0_3_url",
                "class_register_template_4_11_url",
            ]
        )


def put_back(apps, schema_editor):
    SchoolYear = apps.get_model("school_years", "SchoolYear")
    for year in SchoolYear.objects.exclude(class_register_template_4_11_url=""):
        year.class_register_template_url = year.class_register_template_4_11_url
        year.save(update_fields=["class_register_template_url"])


class Migration(migrations.Migration):
    dependencies = [("school_years", "0007_seed_assistant_principals")]

    operations = [
        migrations.AddField(
            model_name="schoolyear",
            name="class_register_template_0_3_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="schoolyear",
            name="class_register_template_4_11_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
        migrations.RunPython(carry_over, put_back),
        migrations.RemoveField(model_name="schoolyear", name="class_register_template_url"),
    ]

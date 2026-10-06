"""Carry the three завучи over from the legacy table, by parallel.

The old table was keyed by class code and missed nineteen of the thirty-two classes. The
people in it were right; only the keys had gone stale. Seeding by parallel both preserves what
worked and fixes what silently did not.

Only an empty table is seeded, so a school that has already filled this in is left alone.
"""

from django.db import migrations

PRINCIPALS = [
    ("Лидия Черкасова | Lydia Cherkasova", [0, 1, 2, 3]),
    ("Александр Белов | Alexander Belov", [4, 5, 6]),
    ("Ольга Ткачук | Olga Tkachuk", [7, 8, 9, 10, 11]),
]


def seed(apps, schema_editor):
    AssistantPrincipal = apps.get_model("school_years", "AssistantPrincipal")
    if AssistantPrincipal.objects.exists():
        return
    for name, parallels in PRINCIPALS:
        AssistantPrincipal.objects.create(name=name, parallels=parallels, is_active=True)


def unseed(apps, schema_editor):
    AssistantPrincipal = apps.get_model("school_years", "AssistantPrincipal")
    AssistantPrincipal.objects.filter(name__in=[name for name, _ in PRINCIPALS]).delete()


class Migration(migrations.Migration):
    dependencies = [("school_years", "0006_assistantprincipal")]
    operations = [migrations.RunPython(seed, unseed)]

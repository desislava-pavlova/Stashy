"""Starter categories (Phase 12): a plain text file at the project root
(basic_categories.txt), one top-level category name per line - kept as a file rather than a
Python list so it can be edited without touching code. A name containing "income"
(case-insensitive) becomes an Income category; everything else becomes Expense - simple
enough to infer from the name itself, so the file doesn't need its own type column.
"""
from pathlib import Path

from django.conf import settings

from .models import Category

STARTER_CATEGORIES_FILENAME = "basic_categories.txt"


def starter_categories_path():
    return Path(settings.BASE_DIR) / STARTER_CATEGORIES_FILENAME


def starter_category_names():
    """Every non-blank line in the file, in order; [] if the file doesn't exist."""
    path = starter_categories_path()
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def add_starter_categories():
    """Creates any starter category that doesn't already exist yet (matched by name,
    case-insensitive, among top-level categories) - safe to run more than once, e.g. after
    adding a line to the file. Returns the list of names actually created."""
    existing = {name.lower() for name in Category.objects.filter(parent__isnull=True).values_list("name", flat=True)}
    created = []
    for name in starter_category_names():
        if name.lower() in existing:
            continue
        category_type = Category.CategoryType.INCOME if "income" in name.lower() else Category.CategoryType.EXPENSE
        Category.objects.create(name=name, category_type=category_type)
        existing.add(name.lower())
        created.append(name)
    return created

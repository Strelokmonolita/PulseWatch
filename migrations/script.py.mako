"""Alembic script configuration."""
import os
from alembic import op
from datetime import datetime


def get_rev_id() -> str:
    """Generate revision ID from timestamp."""
    return datetime.now().strftime("%Y%m%d%H%M%S")


# This is a placeholder; actual script generation happens via alembic command

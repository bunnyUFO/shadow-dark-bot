"""character effects remember the library template they were copied from

Revision ID: 0021_character_effect_template
Revises: 0020_effect_templates
Create Date: 2026-07-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_character_effect_template"
down_revision: Union[str, None] = "0020_effect_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("character_effects") as batch_op:
        batch_op.add_column(sa.Column("template_id", sa.Integer))
        batch_op.create_foreign_key(
            "fk_character_effects_template",
            "effect_templates",
            ["template_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("character_effects") as batch_op:
        batch_op.drop_constraint("fk_character_effects_template", type_="foreignkey")
        batch_op.drop_column("template_id")

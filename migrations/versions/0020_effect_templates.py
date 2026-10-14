"""effects library: effect templates and their links to items/spells/creatures

Revision ID: 0020_effect_templates
Revises: 0019_character_xp
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_effect_templates"
down_revision: Union[str, None] = "0019_character_xp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "effect_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("max_rounds", sa.Integer),
        sa.Column("bonuses", sa.Text),
        sa.Column("created_by", sa.String),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint(
            "max_rounds IS NULL OR (max_rounds >= 1 AND max_rounds <= 100)",
            name="ck_effect_templates_rounds_range",
        ),
    )
    op.create_index(
        "uq_effect_templates_name_ci", "effect_templates",
        [sa.text("lower(name)")], unique=True,
    )
    op.create_table(
        "effect_template_links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "template_id", sa.Integer,
            sa.ForeignKey("effect_templates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("target_id", sa.Integer, nullable=False),
        sa.CheckConstraint(
            "kind IN ('item', 'spell', 'creature')", name="ck_effect_links_kind"
        ),
        sa.UniqueConstraint(
            "template_id", "kind", "target_id", name="uq_effect_template_link"
        ),
    )
    op.create_index(
        "ix_effect_links_target", "effect_template_links", ["kind", "target_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_effect_links_target", "effect_template_links")
    op.drop_table("effect_template_links")
    op.drop_index("uq_effect_templates_name_ci", "effect_templates")
    op.drop_table("effect_templates")

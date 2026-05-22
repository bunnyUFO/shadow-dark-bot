"""add bundle_size to items

Revision ID: 0005_bundle_size
Revises: 0004_coffers
Create Date: 2026-05-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_bundle_size"
down_revision: Union[str, None] = "0004_coffers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "bundle_size",
                sa.Integer,
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.create_check_constraint(
            "ck_items_bundle_size_positive", "bundle_size >= 1"
        )


def downgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_constraint("ck_items_bundle_size_positive", type_="check")
        batch_op.drop_column("bundle_size")

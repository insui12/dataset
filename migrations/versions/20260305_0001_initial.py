"""Initial schema for GBTD raw-infra."""

from alembic import op

from gbtd_infra import models

# revision identifiers, used by Alembic.
revision = "202603050001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    models.Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    models.Base.metadata.drop_all(bind)

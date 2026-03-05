"""${message}"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '${revision}'
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades}


def downgrade() -> None:
    ${downgrades}

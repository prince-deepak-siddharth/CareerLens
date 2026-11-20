"""Record the encoder used for a saved profile embedding."""

import sqlalchemy as sa

from alembic import op

revision = "5b596c42651a"
down_revision = "8e2b91f45c30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_profiles", sa.Column("embedding_model", sa.String(200), nullable=True))


def downgrade():
    op.drop_column("user_profiles", "embedding_model")

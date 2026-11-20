"""Allow job feeds to leave unreported metadata empty."""

import sqlalchemy as sa

from alembic import op

revision = "8e2b91f45c30"
down_revision = "25a4f9693dff"
branch_labels = None
depends_on = None

OPTIONAL_FIELDS = (
    "min_years",
    "employment_type",
    "location",
    "remote_policy",
    "company_category",
    "posted_at",
)


def upgrade():
    for name in OPTIONAL_FIELDS:
        op.alter_column("job_postings", name, nullable=True)


def downgrade():
    conditions = " OR ".join(f"{name} IS NULL" for name in OPTIONAL_FIELDS)
    missing = op.get_bind().execute(sa.text(f"SELECT 1 FROM job_postings WHERE {conditions} LIMIT 1")).first()
    if missing:
        raise RuntimeError("Older schemas cannot represent postings with unknown metadata")
    for name in OPTIONAL_FIELDS:
        op.alter_column("job_postings", name, nullable=False)

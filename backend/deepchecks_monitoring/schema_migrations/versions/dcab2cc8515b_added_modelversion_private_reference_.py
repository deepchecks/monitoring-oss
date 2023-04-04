# ----------------------------------------------------------------------------
# Copyright (C) 2021-2022 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
"""added ModelVersion.private_reference_columns

Revision ID: dcab2cc8515b
Revises: 0d2eb31dab85
Create Date: 2023-02-22 15:00:54.700735

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'dcab2cc8515b'
down_revision = '0d2eb31dab85'
branch_labels = None
depends_on = None


# WARNING: this is a dengereous migration!
# it adds a new column to references table of each organization
# and by doing so it affectevly blocks any read/write operations
# to those tables


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        'model_versions',
        sa.Column(
            'private_reference_columns',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb")
        )
    )

    model_version = sa.table(
        "model_versions",
        sa.column("id", sa.Integer),
        sa.column("model_id", sa.Integer),
        sa.column("private_reference_columns", postgresql.JSONB)
    )

    connection = op.get_bind()
    model_versions = connection.execute(sa.select(model_version.c.id, model_version.c.model_id)).all()

    for (version_id, model_id) in model_versions:
        reference_table_name = f"model_{model_id}_ref_data_{version_id}"

        op.add_column(
            reference_table_name,
            sa.Column('_dc_ref_sample_id', sa.Integer, primary_key=True)
        )
        op.create_index(
            f'_{reference_table_name}_md5_index',
            reference_table_name,
            [sa.text(f'md5(_dc_ref_sample_id::varchar)')]
        )
        connection.execute(
            sa.update(model_version)
            .where(model_version.c.id == version_id)
            .values({model_version.c.private_reference_columns: {"_dc_ref_sample_id": "integer"}})
        )

    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('model_versions', 'private_reference_columns')

    model_version = sa.table(
        "model_versions",
        sa.column("id", sa.Integer),
        sa.column("model_id", sa.Integer),
    )

    connection = op.get_bind()
    model_versions = connection.execute(sa.select(model_version.c.id, model_version.c.model_id)).all()

    for (version_id, model_id) in model_versions:
        reference_table_name = f"model_{model_id}_ref_data_{version_id}"
        op.drop_index(f'_{reference_table_name}_md5_index', reference_table_name,)
        op.drop_column(reference_table_name, '_dc_ref_sample_id')
    # ### end Alembic commands ###

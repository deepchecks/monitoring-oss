# ----------------------------------------------------------------------------
# Copyright (C) 2021-2022 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
"""add s3 properties

Revision ID: c3a1c066eefc
Revises: 1b0028e8c1e1
Create Date: 2023-05-30 13:46:59.301403

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c3a1c066eefc'
down_revision = 'e8cb07203dd5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('data_sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=True),
        sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.add_column('model_versions', sa.Column('latest_file_time', sa.DateTime(timezone=True), nullable=True))
    op.add_column('models', sa.Column('obj_store_last_scan_time', sa.DateTime(timezone=True), nullable=True))
    op.add_column('models', sa.Column('obj_store_path', sa.String(), nullable=True))
    op.add_column('models', sa.Column('latest_labels_file_time', sa.DateTime(timezone=True), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('models', 'latest_labels_file_time')
    op.drop_column('models', 'obj_store_path')
    op.drop_column('models', 'obj_store_last_scan_time')
    op.drop_column('model_versions', 'latest_file_time')
    op.drop_table('data_sources')
    # ### end Alembic commands ###

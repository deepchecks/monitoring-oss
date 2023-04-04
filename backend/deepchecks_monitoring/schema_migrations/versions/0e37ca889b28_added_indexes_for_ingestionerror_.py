# ----------------------------------------------------------------------------
# Copyright (C) 2021-2022 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
"""added indexes for IngestionError.created_at and IngestionError.error

Revision ID: 0e37ca889b28
Revises: 2aff25ef5915
Create Date: 2022-12-12 07:22:01.284648
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '0e37ca889b28'
down_revision = '2aff25ef5915'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_index(op.f('ix_ingestion_errors_created_at'), 'ingestion_errors', ['created_at'], unique=False)
    op.create_index(op.f('ix_ingestion_errors_error'), 'ingestion_errors', ['error'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_ingestion_errors_error'), table_name='ingestion_errors')
    op.drop_index(op.f('ix_ingestion_errors_created_at'), table_name='ingestion_errors')
    # ### end Alembic commands ###

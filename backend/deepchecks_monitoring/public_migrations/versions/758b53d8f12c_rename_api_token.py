"""rename-api-token

Revision ID: 758b53d8f12c
Revises: ba6a4e4c3661
Create Date: 2022-10-24 14:56:16.245321

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '758b53d8f12c'
down_revision = 'ba6a4e4c3661'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('api_secret_hash', sa.String(), nullable=True))
    op.drop_column('users', 'api_token')
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('api_token', sa.VARCHAR(), autoincrement=False, nullable=True))
    op.drop_column('users', 'api_secret_hash')
    # ### end Alembic commands ###

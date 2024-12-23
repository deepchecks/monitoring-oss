# ----------------------------------------------------------------------------
# Copyright (C) 2021-2022 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
"""Module for the features control."""
from pydantic import BaseModel

__all__ = ["FeaturesControl", "FeaturesSchema"]


class FeaturesSchema(BaseModel):
    """Schema to be returned to the client for the features control."""

    signup_enabled: bool
    slack_enabled: bool
    rows_per_minute: int
    onboarding_enabled: bool
    update_roles: bool
    model_assignment: bool
    email_enabled: bool


class FeaturesControl:
    """Features control class with default parameters for the open source version."""

    def __init__(self, settings):
        self.settings = settings

    async def get_allowed_models(self, session) -> int | None:  # pylint: disable=unused-argument
        """For the cloud, number of models which are allowed by subscription."""
        return 1

    @property
    def signup_enabled(self) -> bool:
        """Whether signup is enabled."""
        return True

    @property
    def slack_enabled(self) -> bool:
        """Whether slack is enabled."""
        return False

    @property
    def onboarding_enabled(self) -> bool:
        """Whether onBoarding is enabled."""
        return False

    @property
    def update_roles(self) -> bool:
        """Whether update_roles is enabled."""
        return False

    @property
    def model_assignment(self) -> bool:
        """Whether model_assignment is enabled."""
        return False

    @property
    def rows_per_minute(self) -> int:
        """Maximum number of rows per minute allowed for organization."""
        return 500_000

    @property
    def multi_tenant(self) -> bool:
        """Whether multi-tenant is enabled."""
        return False

    @property
    def email_enabled(self) -> bool:
        """Whether email is enabled."""
        return bool(self.settings.email_smtp_host)

    def get_all_features(self) -> FeaturesSchema:
        """Get all features for the client."""
        return FeaturesSchema(
            signup_enabled=self.signup_enabled,
            slack_enabled=self.slack_enabled,
            rows_per_minute=self.rows_per_minute,
            onboarding_enabled=self.onboarding_enabled,
            update_roles=self.update_roles,
            model_assignment=self.model_assignment,
            email_enabled=self.email_enabled
        )

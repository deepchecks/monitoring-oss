# ----------------------------------------------------------------------------
# Copyright (C) 2021-2022 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
# pylint: disable=unnecessary-ellipsis
"""Module with resources instantiation logic."""
import logging
import typing as t
from contextlib import asynccontextmanager, contextmanager

import httpx
import redis.exceptions as redis_exceptions
import tenacity
from aiokafka import AIOKafkaProducer
from authlib.integrations.starlette_client import OAuth
from kafka import KafkaAdminClient
from kafka.admin import NewTopic
from kafka.errors import KafkaError, TopicAlreadyExistsError
from redis.client import Redis
from redis.cluster import RedisCluster
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.future.engine import Engine, create_engine
from sqlalchemy.orm import Session

from deepchecks_monitoring import config
from deepchecks_monitoring.features_control import FeaturesControl
from deepchecks_monitoring.integrations.email import EmailSender
from deepchecks_monitoring.logic.cache_functions import CacheFunctions
from deepchecks_monitoring.monitoring_utils import ExtendedAsyncSession, configure_logger, json_dumps
from deepchecks_monitoring.notifications import AlertNotificator
from deepchecks_monitoring.public_models import Organization
from deepchecks_monitoring.public_models.user import User
from deepchecks_monitoring.utils import database
from deepchecks_monitoring.utils.mixpanel import BaseEvent as BaseMixpanelEvent
from deepchecks_monitoring.utils.mixpanel import MixpanelEventReporter
from deepchecks_monitoring.utils.redis_util import create_settings_dict

__all__ = ["ResourcesProvider"]

logger: logging.Logger = configure_logger("server")


class BaseResourcesProvider:
    """Base class for all resources provides."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args, **kwargs):
        await self.async_dispose_resources()

    async def async_dispose_resources(self):
        """Disponse async resources."""
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        self.dispose_resources()

    def dispose_resources(self):
        """Disponse resources."""
        pass


P = t.ParamSpec("P")
TMixpanelEvent = t.TypeVar("TMixpanelEvent", bound=BaseMixpanelEvent)


class ResourcesProvider(BaseResourcesProvider):
    """Provider of resources."""

    ALERT_NOTIFICATOR_TYPE = AlertNotificator

    def __init__(self, settings: config.BaseSettings):
        self._settings = settings
        self._database_engine: t.Optional[Engine] = None
        self._async_database_engine: t.Optional[AsyncEngine] = None
        self._kafka_admin: t.Optional[KafkaAdminClient] = None
        self._redis_client: t.Optional[Redis] = None
        self._cache_funcs: t.Optional[CacheFunctions] = None
        self._email_sender: t.Optional[EmailSender] = None
        self._oauth_client: t.Optional[OAuth] = None
        self._parallel_check_executors = None
        self._mixpanel_event_reporter: MixpanelEventReporter | None = None

    @property
    def email_settings(self) -> config.EmailSettings:
        """Get the email settings."""
        if not isinstance(self._settings, config.EmailSettings):
            raise AssertionError(
                "In order to be able to use email resources "
                "you need to provide instance of 'EmailSettings' "
                "to the 'ResourcesProvider' constructor"
            )
        return self._settings

    @property
    def database_settings(self) -> config.DatabaseSettings:
        """Return database settings."""
        if not isinstance(self._settings, config.DatabaseSettings):
            raise AssertionError(
                "In order to be able to instantiate sqlalchemy resources "
                "you need to provide instance of 'DatabaseSettigns' "
                "to the 'ResourcesProvider' constructor"
            )
        return self._settings

    @property
    def kafka_settings(self) -> config.KafkaSettings:
        """Return kafka settings."""
        if not isinstance(self._settings, config.KafkaSettings):
            raise AssertionError(
                "In order to be able to instantiate kafka resources "
                "you need to provide instance of 'KafkaSettings' "
                "to the 'ResourcesProvider' constructor"
            )
        return self._settings

    @property
    def redis_settings(self) -> config.RedisSettings:
        """Get the redis settings."""
        if not isinstance(self._settings, config.RedisSettings):
            raise AssertionError(
                "In order to be able to instantiate redis resources "
                "you need to provide instance of 'RedisSettings' "
                "to the 'ResourcesProvider' constructor"
            )
        return self._settings

    @property
    def settings(self) -> config.Settings:
        """Get settings."""
        if not isinstance(self._settings, config.Settings):
            raise AssertionError(
                f"Settings instance of unknown type was provided - {type(self._settings)}, "
                "you need to provide instance of 'Settings' "
                "to the 'ResourcesProvider' constructor"
            )
        return self._settings

    def dispose_resources(self):
        """Dispose resources."""
        if self._database_engine is not None:
            self._database_engine.dispose()

    async def async_dispose_resources(self):
        """Dispose async resources."""
        if self._async_database_engine is not None:
            await self._async_database_engine.dispose()

    @property
    def database_engine(self) -> Engine:
        """Return sync database engine."""
        settings = self.database_settings

        if self._database_engine is not None:
            return self._database_engine

        self._database_engine = create_engine(
            str(settings.database_uri),
            echo=settings.echo_sql,
            json_serializer=json_dumps,
            future=True,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=10,
            max_overflow=20
        )

        return self._database_engine

    @contextmanager
    def create_database_session(self) -> t.Iterator[Session]:
        """Create sqlalchemy database session."""
        with Session(
            self.database_engine,
            autoflush=False,
            expire_on_commit=False,
            autocommit=False,
        ) as session:  # pylint: disable=not-callable
            try:
                yield session
                session.commit()
            except Exception as error:
                session.rollback()
                raise error
            finally:
                session.close()

    @property
    def async_database_engine(self) -> AsyncEngine:
        """Return async sqlalchemy database engine."""
        settings = self.database_settings

        if self._async_database_engine:
            return self._async_database_engine

        self._async_database_engine = create_async_engine(
            str(settings.async_database_uri),
            echo=settings.echo_sql,
            json_serializer=json_dumps,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=10,
            max_overflow=20
        )
        return self._async_database_engine

    @t.overload
    def create_async_database_session(
        self,
        organization_id: None = None
    ) -> t.AsyncContextManager[ExtendedAsyncSession]:
        """Create async sqlalchemy database session."""
        ...

    @t.overload
    def create_async_database_session(
        self,
        organization_id: int
    ) -> t.AsyncContextManager[t.Optional[ExtendedAsyncSession]]:
        """Create async sqlalchemy database session."""
        ...

    @asynccontextmanager
    async def create_async_database_session(
        self,
        organization_id: t.Optional[int] = None
    ) -> t.AsyncIterator[t.Optional[ExtendedAsyncSession]]:
        """Create async sqlalchemy database session."""
        async with ExtendedAsyncSession(
            self.async_database_engine,
            autoflush=False,
            expire_on_commit=False,
            autocommit=False,
        ) as session:  # pylint: disable=not-callable
            try:
                if organization_id:
                    organization_schema = await session.scalar(
                        select(Organization.schema_name)
                        .where(Organization.id == organization_id)
                    )
                    if organization_schema is None:
                        yield
                        return
                    await database.attach_schema_switcher_listener(
                        session=session,
                        schema_search_path=[organization_schema, "public"]
                    )
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    @asynccontextmanager
    async def get_kafka_producer(self) -> t.AsyncGenerator[AIOKafkaProducer, None]:
        """Return kafka producer."""
        settings = self.kafka_settings
        if settings.kafka_host is None:
            raise ValueError("No kafka host configured")
        kafka_producer = AIOKafkaProducer(**settings.kafka_params)
        try:
            await kafka_producer.start()
            yield kafka_producer
        finally:
            await kafka_producer.stop()

    @contextmanager
    def get_kafka_admin(self) -> t.Generator[KafkaAdminClient, None, None]:
        """Return kafka admin."""
        settings = self.kafka_settings
        if settings.kafka_host is None:
            raise ValueError("No kafka host configured")
        kafka_admin = KafkaAdminClient(**settings.kafka_params)
        try:
            yield kafka_admin
        finally:
            kafka_admin.close()

    @property
    def redis_client(self) -> t.Optional[Redis]:
        """Return redis client if redis defined, else None."""
        if self._redis_client is None and self.redis_settings.redis_uri:
            settings = create_settings_dict(self.redis_settings)
            try:
                self._redis_client = RedisCluster.from_url(
                    cluster_error_retry_attempts=self.redis_settings.cluster_error_retry_attempts,
                    **settings
                )
            except redis_exceptions.RedisClusterException:
                self._redis_client = Redis.from_url(**settings)

        return self._redis_client

    @property
    def cache_functions(self) -> t.Optional[CacheFunctions]:
        """Return cache functions."""
        if self._cache_funcs is None:
            self._cache_funcs = CacheFunctions(self.redis_client)
        return self._cache_funcs

    @property
    def oauth_client(self):
        """Oauth client."""
        if self._oauth_client is None:
            try:
                url = f"{self.settings.oauth_url}/.well-known/openid-configuration"
                openid_configuration = httpx.get(url).json()
                self._oauth_client = OAuth()
                self._oauth_client.register(
                    name="auth0",
                    client_id=self.settings.oauth_client_id,
                    client_secret=self.settings.oauth_client_secret,
                    access_token_url=openid_configuration["token_endpoint"],
                    access_token_params=None,
                    authorize_url=openid_configuration["authorization_endpoint"],
                    authorize_params={"prompt": "login"},
                    jwks_uri=openid_configuration["jwks_uri"],
                    client_kwargs={"scope": "openid profile email"},
                )
            except Exception as e:
                # TODO:
                # looks weird/
                # maybe better to specify more specific exception type
                # and to wrap with try...except only specific line that
                # might raise that exception
                raise Exception(
                    "There was an error while trying to get the OpenID "
                    "configuration from the server."
                ) from e
        return self._oauth_client

    @property
    def email_sender(self) -> EmailSender:
        """Email sender."""
        if self._email_sender is None:
            self._email_sender = EmailSender(self.settings)
        return self._email_sender

    @property
    def parallel_check_executors_pool(self):
        """Return parallel check executors actors."""
        # pylint: disable=import-outside-toplevel
        try:
            import ray  # noqa
            from ray.util.actor_pool import ActorPool  # noqa
        except ImportError:
            logger.info({"message": "Ray is not installed"})
            return

        if not ray.is_initialized():
            logger.info({
                "message": "Ray is not initialized"
            })
            return

        if pool := getattr(self, "_parallel_check_executors", None):
            return pool

        from deepchecks_monitoring.logic.parallel_check_executor import CheckPerWindowExecutor
        database_uri = str(self.database_settings.database_uri)

        p = self._parallel_check_executors = ActorPool([
            CheckPerWindowExecutor.options(
                name=f"CheckExecutor-{index}",
                get_if_exists=True,
                namespace="check-executors",
                lifetime="detached",
                max_task_retries=-1,
                max_restarts=4,
            ).remote(database_uri)
            for index in range(self.settings.total_number_of_check_executor_actors)
        ])

        return p

    def shutdown_parallel_check_executors_pool(self):
        """Shutdown parallel check executors actors."""
        self._parallel_check_executors = None
        # pylint: disable=import-outside-toplevel
        import ray  # noqa
        ray.shutdown()

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        wait=tenacity.wait_fixed(1),
        retry=tenacity.retry_if_exception_type(KafkaError),
        reraise=True,
        before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
    )
    def ensure_kafka_topic(self, topic_name, num_partitions=1) -> bool:
        """Ensure that kafka topic exist. If not, creating it.

        Returns
        -------
        bool
            True if topic existed, False if was created
        """
        with self.get_kafka_admin() as kafka_admin:
            # Refresh the topics list from the server
            if topic_name in set(kafka_admin.list_topics()):
                return True

            # If still doesn't exist try to create
            try:
                kafka_admin.create_topics([
                    NewTopic(
                        name=topic_name,
                        num_partitions=num_partitions,
                        replication_factor=self.kafka_settings.kafka_replication_factor
                    )
                ])
                return False
            # 2 workers might try to create topic at the same time so ignoring if already exists
            except TopicAlreadyExistsError:
                return True

    async def lazy_report_mixpanel_event(
        self,
        event_factory: t.Callable[P, t.Awaitable[TMixpanelEvent]],
        *args: P.args,
        **kwargs: P.kwargs
    ) -> t.Callable[..., TMixpanelEvent | None]:
        """Create 'report_mixpanel_event' callback for later use."""
        if (mixpanel := self._get_mixpanel_event_reporter()) is None:
            return lambda: None
        else:
            kwargs["settings"] = self.settings
            event = await event_factory(*args, **kwargs)

            def fn():
                nonlocal event, mixpanel
                mixpanel.report(event)
                return event

            return fn

    async def report_mixpanel_event(
        self,
        event_factory: t.Callable[P, t.Awaitable[TMixpanelEvent]],
        *args: P.args,
        **kwargs: P.kwargs
    ) -> TMixpanelEvent | None:
        """Send mixpanel event."""
        if mixpanel := self._get_mixpanel_event_reporter():
            kwargs["settings"] = self.settings
            event = await event_factory(*args, **kwargs)
            mixpanel.report(event)
            return event

    @property
    def is_analytics_enabled(self) -> bool:
        """Check whether analytics is enabled."""
        return self._get_mixpanel_event_reporter() is not None

    def _get_mixpanel_event_reporter(self) -> MixpanelEventReporter | None:
        mixpanel = self._mixpanel_event_reporter

        if mixpanel is not None:
            return mixpanel
        if self.settings.enable_analytics is False:
            logger.warning({"message": "Analytics gathering is disabled"})
            return
        if token := self.settings.mixpanel_id:
            mixpanel = MixpanelEventReporter.from_token(token)
            self._mixpanel_event_reporter = mixpanel
            return mixpanel

        logger.warning({"message": "Mixpanel token is not provided"})

    def get_features_control(self, user: User) -> FeaturesControl:  # pylint: disable=unused-argument
        """Return features control."""
        return FeaturesControl(self.settings)

    def get_client_configuration(self) -> "dict[str, t.Any]":
        """Return configuration to be used in client side."""
        return {
            "environment": None,
            "mixpanel_id": None,
            "is_cloud": False,
            "hotjar_id": None,
            "hotjar_sv": None,
            "datadog_fe_token": None,
        }

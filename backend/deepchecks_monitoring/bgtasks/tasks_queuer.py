# ----------------------------------------------------------------------------
# Copyright (C) 2021-2022 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
#
"""Contains alert scheduling logic."""
import asyncio
import datetime
import logging
import typing as t
from time import perf_counter

import anyio
import pendulum as pdl
import redis.exceptions as redis_exceptions
import sqlalchemy as sa
import uvloop
from redis.asyncio import Redis, RedisCluster
from sqlalchemy.cimmutabledict import immutabledict

from deepchecks_monitoring.bgtasks.alert_task import AlertsTask
from deepchecks_monitoring.bgtasks.delete_db_table_task import DeleteDbTableTask
from deepchecks_monitoring.bgtasks.mixpanel_system_state_event import MixpanelSystemStateEvent
from deepchecks_monitoring.bgtasks.model_data_ingestion_alerter import ModelDataIngestionAlerter
from deepchecks_monitoring.bgtasks.model_version_cache_invalidation import ModelVersionCacheInvalidation
from deepchecks_monitoring.config import DatabaseSettings
from deepchecks_monitoring.logic.keys import GLOBAL_TASK_QUEUE
from deepchecks_monitoring.monitoring_utils import configure_logger
from deepchecks_monitoring.public_models.task import BackgroundWorker, Task
from deepchecks_monitoring.utils.redis_util import init_async_redis

try:
    from deepchecks_monitoring import ee
    from deepchecks_monitoring.ee.resources import ResourcesProvider

    with_ee = True
except ImportError:
    from deepchecks_monitoring.resources import ResourcesProvider
    with_ee = False


class TasksQueuer:
    """Model version worker logic."""

    def __init__(
            self,
            resource_provider: ResourcesProvider,
            redis_client: RedisCluster | Redis,
            workers: t.List[BackgroundWorker],
            logger: logging.Logger,
            run_interval: int,
    ):
        self.resource_provider = resource_provider
        self.logger = logger
        self.run_interval = run_interval
        self.redis = redis_client

        # Build the query once to be used later
        delay_by_type = sa.case(
            [(
                Task.bg_worker_task == bg_worker.queue_name(),
                datetime.timedelta(seconds=bg_worker.delay_seconds())
            ) for bg_worker in workers],
            else_=datetime.timedelta(seconds=0)
        )
        retry_by_type = sa.case(
            [(
                Task.bg_worker_task == bg_worker.queue_name(),
                datetime.timedelta(seconds=bg_worker.retry_seconds())
            ) for bg_worker in workers],
            else_=datetime.timedelta(seconds=200)
        )

        retry_expression = Task.num_pushed * retry_by_type
        next_execution_time = Task.creation_time + delay_by_type + retry_expression

        self.query = (
            sa.update(Task)
            .where(Task.id.in_((
                sa.select(Task.id)
                .where(next_execution_time <= sa.func.statement_timestamp())
                .with_for_update()
            )))
            .values(num_pushed=Task.num_pushed + 1)
            .returning(Task.id, Task.bg_worker_task, Task.num_pushed)
        )

    async def run(self):
        """Run the main loop."""
        try:
            while True:
                async with self.resource_provider.create_async_database_session() as session:
                    start = perf_counter()
                    total = await self.move_tasks_to_queue(session)
                    duration = perf_counter() - start
                    self.logger.info({'num_pushed': total, 'duration': duration})
                await asyncio.sleep(self.run_interval)
        except anyio.get_cancelled_exc_class():
            self.logger.exception('Worker coroutine canceled')
            raise
        except Exception:
            self.logger.exception('Failure')
            raise
        except BaseException:
            self.logger.warning('Worker interrupted')
            raise

    async def move_tasks_to_queue(self, session) -> int:
        """Return the number of queued tasks."""
        # SQLAlchemy evaluates the WHERE criteria in the UPDATE statement in Python, to locate matching objects
        # within the Session and update them. Therefore, we must use synchronize_session=False to tell sqlalchemy
        # that we don't care about updating ORM objects in the session.
        tasks = (await session.execute(self.query, execution_options=immutabledict({'synchronize_session': False})))\
            .all()
        ts = pdl.now().int_timestamp
        task_ids = {x['id']: ts for x in tasks}
        if task_ids:
            try:
                # Push to sorted set. if task id is already in set then do nothing.
                pushed_count = await self.redis.zadd(GLOBAL_TASK_QUEUE, task_ids, nx=True)
                for task in tasks:
                    task_id = task['id']
                    worker = task['bg_worker_task']
                    num_pushed = task['num_pushed']
                    self.logger.info(f'pushing task {task_id} for {worker} that was pushed {num_pushed}')
                return pushed_count
            except redis_exceptions.ConnectionError:
                # If redis failed, does not commit the update to the db
                self.logger.error('Failed connecting to redis')
                await session.rollback()
        return 0


class WorkerSettings(DatabaseSettings):
    """Worker settings."""

    logfile: t.Optional[str] = None
    loglevel: str = 'INFO'
    logfile_maxsize: int = 10000000  # 10MB
    logfile_backup_count: int = 3
    queuer_run_interval: int = 30

    class Config:
        """Model config."""

        env_file = '.env'
        env_file_encoding = 'utf-8'


def execute_worker():
    """Execute worker."""

    async def main():
        settings = WorkerSettings()
        service_name = 'tasks-queuer'

        logger = configure_logger(
            name=service_name,
            log_level=settings.loglevel,
            logfile=settings.logfile,
            logfile_backup_count=settings.logfile_backup_count,
        )

        # When running main it creates TaskQueuer under __main__ module, which fails
        # the telemetry collection. Adding here this import to fix this
        from deepchecks_monitoring.bgtasks import tasks_queuer  # pylint: disable=import-outside-toplevel

        workers = [
            # ModelVersionTopicDeletionWorker,
            ModelVersionCacheInvalidation,
            ModelDataIngestionAlerter,
            DeleteDbTableTask,
            AlertsTask,
            MixpanelSystemStateEvent
        ]

        # Add ee workers
        if with_ee:
            workers.append(ee.bgtasks.ObjectStorageIngestor)

        async with ResourcesProvider(settings) as rp:
            async with anyio.create_task_group() as g:
                async_redis = await init_async_redis()
                worker = tasks_queuer.TasksQueuer(rp, async_redis, workers, logger, settings.queuer_run_interval)
                g.start_soon(worker.run)

    uvloop.install()
    anyio.run(main)


if __name__ == '__main__':
    execute_worker()

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
import logging
import typing as t

import anyio
import pendulum as pdl
import redis.exceptions as redis_exceptions
import uvloop
from redis.asyncio import Redis, RedisCluster
from sqlalchemy import select

from deepchecks_monitoring.bgtasks.alert_task import AlertsTask
from deepchecks_monitoring.bgtasks.delete_db_table_task import DeleteDbTableTask
from deepchecks_monitoring.bgtasks.mixpanel_system_state_event import MixpanelSystemStateEvent
from deepchecks_monitoring.bgtasks.model_data_ingestion_alerter import ModelDataIngestionAlerter
from deepchecks_monitoring.bgtasks.model_version_cache_invalidation import ModelVersionCacheInvalidation
from deepchecks_monitoring.config import Settings
from deepchecks_monitoring.logic.keys import GLOBAL_TASK_QUEUE, TASK_RUNNER_LOCK
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


class TaskRunner:
    """Task runner."""

    def __init__(
            self,
            resource_provider: ResourcesProvider,
            redis: RedisCluster | Redis,
            workers: t.List[BackgroundWorker],
            logger: logging.Logger,
    ):
        self.resource_provider = resource_provider
        self.redis = redis
        self.logger = logger
        self.workers = {w.queue_name(): w for w in workers}

    async def run(self):
        """Run the main loop."""
        try:
            while True:
                task = await self.wait_for_task()
                if task:
                    task_id, queued_timestamp = task
                    async with self.resource_provider.create_async_database_session() as session:
                        await self.run_single_task(task_id, session, queued_timestamp)

        except anyio.get_cancelled_exc_class():
            self.logger.exception('Worker coroutine canceled')
            raise
        except Exception:
            self.logger.exception('Failure')
            raise
        except BaseException:
            self.logger.warning('Worker interrupted')
            raise

    async def wait_for_task(self, timeout=120):
        task_entry = await self.redis.bzpopmin(GLOBAL_TASK_QUEUE, timeout=timeout)
        if task_entry is None:
            self.logger.info('Got from redis queue task_id none')
            return
        # Return value from redis is (redis key, value, score)
        task_id = int(task_entry[1].decode())
        queued_timestamp: int = task_entry[2]
        return task_id, queued_timestamp

    async def run_single_task(self, task_id, session, queued_timestamp):
        """Run single task."""
        # Using distributed lock to make sure long task won't be ran twice
        lock_name = TASK_RUNNER_LOCK.format(task_id)
        # By default, allow task 5 minutes before removes lock to allow another run. Inside the task itself we can
        # extend the lock if we are doing slow operation and want more time
        lock = self.redis.lock(lock_name, blocking=False, timeout=60 * 5)
        lock_acquired = await lock.acquire()
        if not lock_acquired:
            self.logger.info(f'Failed to acquire lock for task id: {task_id}')
            return

        task: Task = await session.scalar(select(Task).where(Task.id == task_id))
        # Making sure task wasn't deleted for some reason
        if task is not None:
            self.logger.info(f'Running task id: {task_id} for {task.bg_worker_task}')
            await self._run_task(task, session, queued_timestamp, lock)
        else:
            self.logger.info(f'Got already removed task id: {task_id}')

        try:
            await lock.release()
        except redis_exceptions.LockNotOwnedError:
            self.logger.error(f'Failed to release lock for task id: {task_id}. probably task run for longer than '
                              f'maximum time for the lock')

    async def _run_task(self, task: Task, session, queued_timestamp, lock):
        """Inner function to run task, created in order to wrap in the telemetry instrumentor and be able
        to log the task parameters and queued time."""
        bg_worker_task = task.bg_worker_task
        try:
            worker: BackgroundWorker = self.workers.get(bg_worker_task)
            if worker:
                start = pdl.now()
                await worker.run(task, session, self.resource_provider, lock)
                duration = (pdl.now() - start).total_seconds()
                delay = start.int_timestamp - queued_timestamp
                self.logger.info({'duration': duration, 'task': bg_worker_task, 'delay': delay})
            else:
                self.logger.error({'message': f'Unknown task type: {bg_worker_task}'})
        except Exception:  # pylint: disable=broad-except
            self.logger.exception({'message': 'Exception running task', 'task': bg_worker_task})
            await session.rollback()


class BaseWorkerSettings():
    """Worker settings."""

    logfile: t.Optional[str] = None
    loglevel: str = 'DEBUG'
    logfile_maxsize: int = 10000000  # 10MB
    logfile_backup_count: int = 3
    num_workers: int = 5

    class Config:
        """Model config."""

        env_file = '.env'
        env_file_encoding = 'utf-8'


if with_ee:
    class WorkerSettings(BaseWorkerSettings, ee.config.Settings):
        """Set of worker settings."""
        pass
else:
    class WorkerSettings(BaseWorkerSettings, Settings):
        """Set of worker settings."""
        pass


def execute_worker():
    """Execute worker."""

    async def main():
        settings = WorkerSettings()
        service_name = 'tasks-runner'

        logger = configure_logger(
            name=service_name,
            log_level=settings.loglevel,
            logfile=settings.logfile,
            logfile_backup_count=settings.logfile_backup_count,
        )

        # When running main it creates TaskRunner under __main__ module, which fails
        # the telemetry collection. Adding here this import to fix this
        from deepchecks_monitoring.bgtasks import tasks_runner  # pylint: disable=import-outside-toplevel

        async with ResourcesProvider(settings) as rp:
            async_redis = await init_async_redis()

            workers = [
                ModelVersionCacheInvalidation(),
                ModelDataIngestionAlerter(),
                DeleteDbTableTask(),
                AlertsTask(),
                MixpanelSystemStateEvent()
            ]

            # Adding ee workers
            if with_ee:
                workers.append(ee.bgtasks.ObjectStorageIngestor(rp))

            async with anyio.create_task_group() as g:
                worker = tasks_runner.TaskRunner(rp, async_redis, workers, logger)
                for _ in range(settings.num_workers):
                    g.start_soon(worker.run)

    uvloop.install()
    anyio.run(main)


if __name__ == '__main__':
    execute_worker()

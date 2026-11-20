from functools import lru_cache

from redis import Redis
from rq import Queue, Retry, Worker

from .config import settings
from .observability import telemetry


@lru_cache
def redis():
    return Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=3)


def queue():
    return Queue("careerlens", connection=redis(), default_timeout=900)


def enqueue(action, *args, retry=True):
    job = queue().enqueue(action, *args, retry=Retry(max=2, interval=[10, 30]) if retry else None)
    return job.id


class CareerWorker(Worker):
    def perform_job(self, job, queue):
        try:
            return super().perform_job(job, queue)
        finally:
            telemetry.flush()


def main():
    # RQ's blocking worker commands need a longer socket timeout than request paths.
    connection = Redis.from_url(settings().redis_url, socket_connect_timeout=2)
    CareerWorker(["careerlens"], connection=connection).work(with_scheduler=True)


if __name__ == "__main__":
    main()

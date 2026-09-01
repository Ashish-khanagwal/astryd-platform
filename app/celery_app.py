"""Celery integration that runs tasks inside Flask's application context."""

from celery import Celery, Task


def create_celery(app):
    celery = Celery(app.import_name)
    celery.conf.update(
        broker_url=app.config["CELERY_BROKER_URL"],
        result_backend=app.config["CELERY_RESULT_BACKEND"],
        task_track_started=app.config["CELERY_TASK_TRACK_STARTED"],
        task_time_limit=app.config["CELERY_TASK_TIME_LIMIT"],
    )

    class FlaskTask(Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = FlaskTask
    celery.autodiscover_tasks(["app.notifications"])
    return celery

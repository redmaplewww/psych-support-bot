from psych_support_bot.infra.queue.celery_app import celery_app

celery_app.autodiscover_tasks(["psych_support_bot.workers"])

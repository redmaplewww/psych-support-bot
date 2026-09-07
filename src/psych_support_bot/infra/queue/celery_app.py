from celery import Celery

from psych_support_bot.infra.config.settings import get_settings

settings = get_settings()

celery_app = Celery("psych_support_bot", broker=settings.redis_url, backend=settings.redis_url)

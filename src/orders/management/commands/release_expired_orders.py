from django.core.management.base import BaseCommand

from orders.services import release_expired_orders


class Command(BaseCommand):
    help = (
        "Expire unpaid orders past their reservation time and "
        "give their stock "
        "back. Celery beat runs this every minute; the command is a "
        "manual/cron fallback."
    )

    def handle(self, *args, **options):
        count = release_expired_orders()
        self.stdout.write(self.style.SUCCESS(f"{count} order(s) expired."))

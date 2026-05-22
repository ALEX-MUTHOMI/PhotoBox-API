from django.core import mail


def pytest_configure():
    if not hasattr(mail, "outbox"):
        mail.outbox = []

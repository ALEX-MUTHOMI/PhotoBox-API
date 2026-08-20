def test_celery_app_imports():
    from app.celery import app

    assert app.main == "app"

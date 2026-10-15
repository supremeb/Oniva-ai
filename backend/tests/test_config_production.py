"""Production config validation (U10 / R11)."""
import pytest

from app.config import Settings

GOOD_SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
GOOD_SALT = "a3f1c9d27e5b4086a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718"
APP_URL = "postgresql+asyncpg://oniva_app:Xk29-vQpLm7sRt4WzYb8@postgres-primary:5432/oniva"
SYS_URL = "postgresql+asyncpg://oniva_system:Hd71-cNw3KfQe9UxTa5J@postgres-primary:5432/oniva"
OWNER_URL = "postgresql+asyncpg://oniva_owner:Rt58-yLb2VmZc6PsGd1N@postgres-primary:5432/oniva"


def make(**overrides) -> Settings:
    base = dict(
        ENVIRONMENT="production",
        DEBUG=False,
        SECRET_KEY=GOOD_SECRET,
        ENCRYPTION_SALT=GOOD_SALT,
        CORS_ORIGINS="https://app.example.org",
        DATABASE_URL=APP_URL,
        SYSTEM_DATABASE_URL=SYS_URL,
        MIGRATION_DATABASE_URL=OWNER_URL,
        APP_DATABASE_PASSWORD="Xk29-vQpLm7sRt4WzYb8",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_valid_production_config_passes():
    make().validate_production_security()


def test_url_with_postgres_in_user_and_host_is_accepted():
    url = "postgresql+asyncpg://postgres_app:Xk29-vQpLm7sRt4WzYb8@postgres:5432/test_postgres"
    make(DATABASE_URL=url).validate_production_security()


@pytest.mark.parametrize("pw", ["postgres", "password", "changeme", "CHANGE_ME_generate_secure_password"])
def test_weak_db_password_rejected(pw):
    url = f"postgresql+asyncpg://oniva_app:{pw}@db:5432/oniva"
    with pytest.raises(ValueError, match="DATABASE_URL has a missing or weak"):
        make(DATABASE_URL=url).validate_production_security()


def test_missing_db_password_rejected():
    with pytest.raises(ValueError, match="weak"):
        make(DATABASE_URL="postgresql+asyncpg://oniva_app@db:5432/oniva").validate_production_security()


def test_weak_system_and_app_role_passwords_rejected():
    with pytest.raises(ValueError, match="SYSTEM_DATABASE_URL has a missing or weak"):
        make(SYSTEM_DATABASE_URL="postgresql+asyncpg://oniva_system:password@db/oniva").validate_production_security()
    with pytest.raises(ValueError, match="APP_DATABASE_PASSWORD"):
        make(APP_DATABASE_PASSWORD="changeme").validate_production_security()


def test_system_url_required_in_production():
    with pytest.raises(ValueError, match="SYSTEM_DATABASE_URL must be set"):
        make(SYSTEM_DATABASE_URL="").validate_production_security()


def test_app_url_must_not_be_superuser_or_owner():
    with pytest.raises(ValueError, match="non-superuser"):
        make(DATABASE_URL="postgresql+asyncpg://postgres:Xk29-vQpLm7sRt4WzYb8@db/oniva").validate_production_security()
    with pytest.raises(ValueError, match="schema owner"):
        make(DATABASE_URL=OWNER_URL).validate_production_security()
    with pytest.raises(ValueError, match="SYSTEM_DATABASE_URL user"):
        make(DATABASE_URL=SYS_URL).validate_production_security()


def test_default_secret_key_rejected():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        make(SECRET_KEY="your-secret-key-change-in-production-must-be-at-least-32-chars-long").validate_production_security()


def test_default_salt_and_wildcard_cors_rejected():
    with pytest.raises(ValueError, match="ENCRYPTION_SALT"):
        make(ENCRYPTION_SALT="default-salt-change-in-production").validate_production_security()
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        make(CORS_ORIGINS="*").validate_production_security()


def test_error_never_echoes_password():
    url = "postgresql+asyncpg://oniva_app:postgres@db:5432/oniva"
    with pytest.raises(ValueError) as exc:
        make(DATABASE_URL=url).validate_production_security()
    assert "oniva_app:postgres@" not in str(exc.value)


def test_development_is_not_validated():
    Settings(_env_file=None, ENVIRONMENT="development").validate_production_security()

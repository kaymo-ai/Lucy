"""Configuration, read from the environment exactly once.

Everything here has a default that works for the test suite. None of them are
defaults that would be safe in production -- a container started without
LUCY_JOIN_CODE would otherwise admit anybody who guessed "test-code", which is
the kind of thing nobody notices until the camp feed has a stranger in it.
`require_production_config` is what makes that loud instead.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

TEST_JOIN_CODE = "test-code"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:lucytest@localhost:55432/lucytest"))
    join_code: str = field(default_factory=lambda: os.environ.get(
        "LUCY_JOIN_CODE", TEST_JOIN_CODE))
    media_root: Path = field(default_factory=lambda: Path(
        os.environ.get("LUCY_MEDIA_ROOT", "/tmp/lucy-media")))
    # The codeword for the instructions page. Separate from the join code: one
    # is typed once into a web page by anyone the link is forwarded to, the
    # other is typed into the app and joins a phone to the camp feed. Sharing
    # a value between those would mean the page's readership and the feed's
    # membership could never diverge.
    site_code: str = field(default_factory=lambda: os.environ.get(
        "LUCY_SITE_CODE", "test-site-code"))
    site_root: Path = field(default_factory=lambda: Path(
        os.environ.get("LUCY_SITE_ROOT", "/srv/site")))
    # The QA page. Its own secret, because the camp codeword goes to forty
    # people and unlocks install instructions, while this shows every note in
    # the camp and which install sent it.
    admin_code: str = field(default_factory=lambda: os.environ.get(
        "LUCY_ADMIN_CODE", "test-admin-code"))


settings = Settings()


def require_production_config() -> None:
    """Refuses to start with the test join code in a real deployment.

    Called from the app's lifespan when LUCY_ENV=production. A camp code is
    the only thing standing between the feed and the internet, and the failure
    mode of shipping the default is silent.
    """
    if os.environ.get("LUCY_ENV") != "production":
        return
    if settings.join_code == TEST_JOIN_CODE or not settings.join_code:
        raise RuntimeError(
            "LUCY_JOIN_CODE is unset or still the test value; refusing to start")
    if settings.admin_code == "test-admin-code" or not settings.admin_code:
        raise RuntimeError(
            "LUCY_ADMIN_CODE is unset or still the test value; refusing to start")

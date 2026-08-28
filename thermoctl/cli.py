import uvicorn

from thermoctl.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "thermoctl.app:create_app",
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        log_config=None,
    )

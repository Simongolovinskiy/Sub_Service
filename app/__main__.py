import uvicorn

from app.infrastructure.adapters.vpn.settings import LaunchSettings


def main() -> None:
    settings = LaunchSettings()
    uvicorn.run(
        'app.application.factory:create_app', factory=True, host='0.0.0.0',
        port=settings.port, workers=settings.workers,
        log_config='app/infrastructure/adapters/_logging/uvicorn.json',
        timeout_graceful_shutdown=0.5,
    )


if __name__ == '__main__':
    main()

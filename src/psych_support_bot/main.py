import uvicorn

from psych_support_bot.app import create_app


def main() -> None:
    app = create_app()
    print(f"App ready: {app.title}")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

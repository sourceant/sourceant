from .application import create_default_mcp_server


def main() -> None:
    create_default_mcp_server().run()


if __name__ == "__main__":
    main()

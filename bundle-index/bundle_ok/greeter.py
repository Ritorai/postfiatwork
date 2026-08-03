"""A tiny, deliberately boring module used as fixture content."""


def greet(name: str) -> str:
    return "Hello, %s!" % name


if __name__ == "__main__":
    import sys
    who = sys.argv[2] if len(sys.argv) > 2 else "World"
    print(greet(who))

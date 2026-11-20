"""Frozen macOS entry point."""
import sys


def main():
    if '--review-smoke-test' in sys.argv:
        from review_gui import smoke_test
        return smoke_test()
    from app import main as run
    return run()


if __name__ == '__main__':
    sys.exit(main())

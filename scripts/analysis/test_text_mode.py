"""Compatibility entry for the production text resource regression suite.

The previous transport fixture is preserved in the dated baseline source archive.
The unified suite compiles current Java/C++ and executes the actual Lua boot path.
"""
from run_text_resource_candidate import checked_main


def main():
    return checked_main()


if __name__ == "__main__":
    raise SystemExit(main())

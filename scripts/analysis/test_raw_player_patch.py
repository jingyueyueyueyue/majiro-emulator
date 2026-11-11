"""Run the unified production raw/source text resource suite.

The original fixture and its formal metadata-classification fix are preserved
in 20261111_integration_preimages.zip. The default generator destination stays
localization/player/ruri; this regression never regenerates user translations.
"""
from pathlib import Path
from run_text_resource_candidate import checked_main


def main():
    generator=Path(__file__).resolve().parents[1]/"build/prepare_player_patch.py"
    assert "localization/player/ruri/majiro_metadata.lua" in generator.read_text(encoding="utf8")
    return checked_main()


if __name__ == "__main__":
    raise SystemExit(main())

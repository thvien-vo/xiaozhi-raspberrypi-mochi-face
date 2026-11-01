#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automatic wake word generator tool.

Features:
1. Automatically convert Chinese text input into toned Pinyin
2. Split Pinyin by initials and finals
3. Verify whether tokens exist in tokens.txt
4. Automatically generate keywords.txt format
"""

import sys
from pathlib import Path

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:
    print("❌ Missing dependency: pypinyin")
    print("Please install: pip install pypinyin")
    sys.exit(1)


class KeywordGenerator:
    def __init__(self, model_dir: Path):
        """Initialize the wake word generator.

        Args:
            model_dir: Model directory path (containing tokens.txt and keywords.txt)
        """
        self.model_dir = Path(model_dir)
        self.tokens_file = self.model_dir / "tokens.txt"
        self.keywords_file = self.model_dir / "keywords.txt"

        # Load existing tokens
        self.available_tokens = self._load_tokens()

        # Table of initials (to be split)
        self.initials = [
            "b",
            "p",
            "m",
            "f",
            "d",
            "t",
            "n",
            "l",
            "g",
            "k",
            "h",
            "j",
            "q",
            "x",
            "zh",
            "ch",
            "sh",
            "r",
            "z",
            "c",
            "s",
            "y",
            "w",
        ]

    def _load_tokens(self) -> set:
        """
        Load all available tokens from tokens.txt.
        """
        if not self.tokens_file.exists():
            print(f"⚠️  Warning: tokens file does not exist: {self.tokens_file}")
            return set()

        tokens = set()
        with open(self.tokens_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    # Format: "token id" or "token"
                    parts = line.split()
                    if parts:
                        tokens.add(parts[0])

        print(f"✅ Loaded {len(tokens)} available tokens")
        return tokens

    def _split_pinyin(self, pinyin: str) -> list:
        """Split Pinyin by initial and final.

        Example: "xiǎo" -> ["x", "iǎo"], "mǐ" -> ["m", "ǐ"], "ài" -> ["ài"] (zero initial)
        """
        if not pinyin:
            return []

        # Match initials by length first (zh, ch, sh prioritized)
        for initial in sorted(self.initials, key=len, reverse=True):
            if pinyin.startswith(initial):
                final = pinyin[len(initial) :]
                if final:
                    return [initial, final]
                else:
                    return [initial]

        # No initial (zero initial)
        return [pinyin]

    def chinese_to_keyword_format(self, chinese_text: str) -> str:
        """Convert Chinese text to keyword format.

        Args:
            chinese_text: Chinese text, e.g., "xiaomi"

        Returns:
            keyword format, e.g., "x iǎo m ǐ x iǎo m ǐ @xiaomi"
        """
        # Convert to toned Pinyin
        pinyin_list = lazy_pinyin(chinese_text, style=Style.TONE)

        # Split each Pinyin syllable
        split_parts = []
        missing_tokens = []

        for pinyin in pinyin_list:
            parts = self._split_pinyin(pinyin)

            # Verify whether each part is in available tokens
            for part in parts:
                if part not in self.available_tokens:
                    missing_tokens.append(part)
                split_parts.append(part)

        # Join results
        pinyin_str = " ".join(split_parts)
        keyword_line = f"{pinyin_str} @{chinese_text}"

        # Warn if any tokens are missing
        if missing_tokens:
            print(
                f"⚠️  Warning: the following tokens are not in tokens.txt: {', '.join(set(missing_tokens))}"
            )
            print("   The generated keyword may not work properly")

        return keyword_line

    def add_keyword(self, chinese_text: str, append: bool = True) -> bool:
        """Add a wake word to keywords.txt.

        Args:
            chinese_text: Wake word text
            append: Whether to append (True) or overwrite (False)

        Returns:
            Whether the operation succeeded
        """
        try:
            # Generate keyword format
            keyword_line = self.chinese_to_keyword_format(chinese_text)

            # Check if it already exists
            if self.keywords_file.exists():
                with open(self.keywords_file, "r", encoding="utf-8") as f:
                    content = f.read()
                    if f"@{chinese_text}" in content:
                        print(f"⚠️  Keyword '{chinese_text}' already exists")
                        return False

            # Write to file
            mode = "a" if append else "w"
            with open(self.keywords_file, mode, encoding="utf-8") as f:
                f.write(keyword_line + "\n")

            print(f"✅ Successfully added: {keyword_line}")
            return True

        except Exception as e:
            print(f"❌ Failed to add keyword: {e}")
            return False

    def batch_add_keywords(self, chinese_texts: list, overwrite: bool = False):
        """Add wake words in batch.

        Args:
            chinese_texts: List of wake word strings
            overwrite: Whether to overwrite the original file
        """
        if overwrite:
            print("⚠️  Overwriting existing keywords.txt")

        success_count = 0
        for text in chinese_texts:
            text = text.strip()
            if not text:
                continue

            if self.add_keyword(text, append=not overwrite):
                success_count += 1

            # Append all subsequent entries after the first one
            overwrite = False

        print(f"\n📊 Done: successfully added {success_count}/{len(chinese_texts)} keywords")

    def list_keywords(self):
        """
        List all current keywords.
        """
        if not self.keywords_file.exists():
            print("⚠️  keywords.txt does not exist")
            return

        print(f"\n📄 Current keyword list ({self.keywords_file}):")
        print("-" * 60)

        with open(self.keywords_file, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if line and not line.startswith("#"):
                    # Extract and display the label part
                    if "@" in line:
                        pinyin_part, chinese_part = line.split("@", 1)
                        print(
                            f"{i}. {chinese_part.strip():15s} -> {pinyin_part.strip()}"
                        )
                    else:
                        print(f"{i}. {line}")

        print("-" * 60)


def main():
    """
    Main function.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Automatic wake word generator tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add a single keyword
  python keyword_generator.py -a "xiaomi"

  # Add keywords in batch
  python keyword_generator.py -b "xiaomi" "nihao_xiaozhi" "jarvis"

  # Import in batch from a file (one keyword per line)
  python keyword_generator.py -f keywords_input.txt

  # List current keywords
  python keyword_generator.py -l

  # Test conversion (without writing to file)
  python keyword_generator.py -t "xiaomi"
        """,
    )

    parser.add_argument(
        "-m", "--model-dir", default="models", help="Model directory path (default: models)"
    )

    parser.add_argument("-a", "--add", help="Add a single keyword")

    parser.add_argument(
        "-b", "--batch", nargs="+", help="Add keywords in batch (multiple keywords separated by spaces)"
    )

    parser.add_argument("-f", "--file", help="Import in batch from a file (one keyword per line)")

    parser.add_argument("-l", "--list", action="store_true", help="List all current keywords")

    parser.add_argument("-t", "--test", help="Test conversion (without writing to file)")

    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite mode (clear existing keywords)"
    )

    args = parser.parse_args()

    # Determine model directory
    if Path(args.model_dir).is_absolute():
        model_dir = Path(args.model_dir)
    else:
        # Relative path: relative to the project root directory
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        model_dir = project_root / args.model_dir

    if not model_dir.exists():
        print(f"❌ Model directory does not exist: {model_dir}")
        sys.exit(1)

    print(f"🔧 Using model directory: {model_dir}")

    # Create generator
    generator = KeywordGenerator(model_dir)

    # Execute operations
    if args.test:
        # Test mode
        print("\n🧪 Testing conversion:")
        keyword_line = generator.chinese_to_keyword_format(args.test)
        print(f"   Input: {args.test}")
        print(f"   Output: {keyword_line}")

    elif args.add:
        # Add single keyword
        generator.add_keyword(args.add)

    elif args.batch:
        # Batch add keywords
        generator.batch_add_keywords(args.batch, overwrite=args.overwrite)

    elif args.file:
        # Import from file
        input_file = Path(args.file)
        if not input_file.exists():
            print(f"❌ File does not exist: {input_file}")
            sys.exit(1)

        with open(input_file, "r", encoding="utf-8") as f:
            keywords = [line.strip() for line in f if line.strip()]

        print(f"📥 Importing {len(keywords)} keywords from file")
        generator.batch_add_keywords(keywords, overwrite=args.overwrite)

    elif args.list:
        # List keywords
        generator.list_keywords()

    else:
        # Interactive mode
        print("\n🎤 Wake Word Generator Tool (Interactive Mode)")
        print("Enter a wake word, or press Ctrl+C / type 'q' to quit\n")

        try:
            while True:
                chinese = input("Enter wake word: ").strip()

                if not chinese or chinese.lower() == "q":
                    break

                generator.add_keyword(chinese)
                print()

        except KeyboardInterrupt:
            print("\n\n👋 Exited")

    # Finally list all keywords
    if not args.list and (args.add or args.batch or args.file):
        generator.list_keywords()


if __name__ == "__main__":
    main()

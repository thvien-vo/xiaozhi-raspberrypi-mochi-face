"""
Common utility functions module.

Contains general utility functions such as browser operations, clipboard access, and verification code extraction.
"""

import re
import webbrowser
from typing import Optional

from src.logging import get_logger

logger = get_logger()


def open_url(url: str) -> bool:
    """Open a web URL in the default browser."""
    try:
        success = webbrowser.open(url)
        if success:
            logger.info(f"Successfully opened URL: {url}")
        else:
            logger.warning(f"Failed to open URL: {url}")
        return success
    except Exception as e:
        logger.error(f"Error opening URL: {e}", exc_info=True)
        return False


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the clipboard."""
    try:
        import pyperclip

        pyperclip.copy(text)
        logger.info(f'Text "{text}" copied to clipboard')
        return True
    except ImportError:
        logger.warning("pyperclip module is not installed, cannot copy to clipboard")
        return False
    except Exception as e:
        logger.error(f"Error copying to clipboard: {e}", exc_info=True)
        return False


def extract_verification_code(text: str) -> Optional[str]:
    """Extract a 6-digit verification code from text."""
    try:
        # Activation-related keywords list
        activation_keywords = [
            "login",
            "log in",
            "control panel",
            "activate",
            "activation",
            "verification code",
            "bind device",
            "add device",
            "enter verification code",
            "enter",
            "panel",
            "xiaozhi.me",
            "activation code",
        ]

        # Check whether the text contains any activation-related keyword
        text_lower = text.lower()
        has_activation_keyword = any(
            keyword in text_lower for keyword in activation_keywords
        )

        if not has_activation_keyword:
            logger.debug(
                f"Text does not contain activation keywords, skipping verification code extraction: {text}"
            )
            return None

        # Specific verification code matching patterns
        patterns = [
            r"verification code[:\s]+(\d{6})",
            r"enter verification code[:\s]+(\d{6})",
            r"enter\s*(\d{6})",
            r"code[:\s]+(\d{6})",
            r"activation code[:\s]+(\d{6})",
            r"(\d{6})[,.]",
            r"[,.]\s*(\d{6})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                code = match.group(1)
                logger.info(f"Extracted verification code from text: {code}")
                return code

        # General pattern matching (e.g. spaced digits)
        match = re.search(r"((?:\d\s*){6,})", text)
        if match:
            code = "".join(match.group(1).split())
            if len(code) == 6 and code.isdigit():
                logger.info(
                    f"Extracted verification code from text (general pattern): {code}"
                )
                return code

        logger.warning(f"Failed to find verification code in text: {text}")
        return None
    except Exception as e:
        logger.error(f"Error extracting verification code: {e}", exc_info=True)
        return None


def handle_verification_code(text: str) -> None:
    """Handle verification code: extract and copy to clipboard."""
    code = extract_verification_code(text)
    if code:
        copy_to_clipboard(code)

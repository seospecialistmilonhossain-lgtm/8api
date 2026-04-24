# AppHub Version Configuration
# Update this file when you release a new version of AppHub

VERSION = "8.0.0"
BUILD_NUMBER = 8
MIN_SUPPORTED_BUILD = 1  # Users with build < 1 will be forced to update
RELEASE_DATE = "2026-04-23"

# File Information
DOWNLOAD_URLS = {
    "arm64-v8a": "",
    "armeabi-v7a": "",
    "x86": "",
    "x86_64": "",
    "universal": ""
}
DOWNLOAD_SIZES = {
    "arm64-v8a": 28500000,
    "armeabi-v7a": 26500000,
    "x86": 29000000,
    "x86_64": 30000000,
    "universal": 40000000
}
DOWNLOAD_URL = DOWNLOAD_URLS["universal"]
APK_HASH = ""  # Example SHA-256 Hash for download integrity verification
SIZE_BYTES = DOWNLOAD_SIZES["universal"]

# Update Enforcement
IS_MANDATORY = False  # If True, prompts an update regardless of MIN_SUPPORTED_BUILD

# Telegram Support
TELEGRAM_CHANNEL = "https://t.me/+IDEuHZyD9lc5Y2Jl"

# Changelog Details
CHANGELOG_TITLE = "🎉 What's New in v8.0.0"
CHANGELOG = """
✨ Major Features
• Upgraded ecosystem for handling more websites
• Added hentaiser website support
• New Settings UI/UX with additional features
• Added sports live section
• Added more new channels to the Media section
• Added casting support
• Player UI changes

🛠️ Bug Fixes & Enhancements
• Fixed streaming on Xmoviesforyou
• Fixed lag and volume issues on live channels
• Disabled pimpbunny due to Cloudflare issues
• Many other unlisted improvements
"""

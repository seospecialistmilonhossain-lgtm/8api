# AppHub Version Configuration
# Update this file when you release a new version of AppHub

VERSION = "6.0.0"
BUILD_NUMBER = 6
MIN_SUPPORTED_BUILD = 1  # Users with build < 1 will be forced to update
RELEASE_DATE = "2026-03-27"

# File Information
DOWNLOAD_URLS = {
    "arm64-v8a": "http://apphubx.store/apphub/app-arm64-v8a-release.apk",
    "armeabi-v7a": "",
    "x86": "",
    "x86_64": "",
    "universal": "http://apphubx.store/apphub/app-arm64-v8a-release.apk"
}
DOWNLOAD_SIZES = {
    "arm64-v8a": 28500000,
    "armeabi-v7a": 26500000,
    "x86": 29000000,
    "x86_64": 30000000,
    "universal": 40000000
}
DOWNLOAD_URL = DOWNLOAD_URLS["universal"]
APK_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # Example SHA-256 Hash for download integrity verification
SIZE_BYTES = DOWNLOAD_SIZES["universal"]

# Update Enforcement
IS_MANDATORY = True  # If True, prompts an update regardless of MIN_SUPPORTED_BUILD

# Telegram Support
TELEGRAM_CHANNEL = "https://t.me/+IDEuHZyD9lc5Y2Jl"

# Changelog Details
CHANGELOG_TITLE = "🎉 What's New in v6.0.0"
CHANGELOG = """
🛠️ Bug Fixes & Enhancements
• Fixed occasional crashes on older devices
• Improved network error handling
• Enhanced stability and reliability
"""

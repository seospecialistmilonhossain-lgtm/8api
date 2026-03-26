# AppHub Version Configuration
# Update this file when you release a new version of AppHub

VERSION = "6.0.0"
BUILD_NUMBER = 6
MIN_SUPPORTED_BUILD = 1  # Users with build < 1 will be forced to update
RELEASE_DATE = "2026-03-27T19:00:00Z"

# File Information
DOWNLOAD_URLS = {
    "arm64-v8a": "https://apphubx.netlify.app/assets/app-arm64-v8a-release.apk",
    "armeabi-v7a": "https://apphubx.netlify.app/assets/app-armeabi-v7a-release.apk",
    "x86": "https://apphubx.netlify.app/assets/app-x86-release.apk",
    "x86_64": "https://apphubx.netlify.app/assets/app-x86_64-release.apk",
    "universal": "https://apphubx.netlify.app/assets/app-universal-release.apk"
}
DOWNLOAD_SIZES = {
    "arm64-v8a": 28500000,
    "armeabi-v7a": 26500000,
    "x86": 29000000,
    "x86_64": 30000000,
    "universal": 58000000
}
DOWNLOAD_URL = DOWNLOAD_URLS["universal"]
APK_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # Example SHA-256 Hash for download integrity verification
SIZE_BYTES = DOWNLOAD_SIZES["universal"]

# Update Enforcement
IS_MANDATORY = True  # If True, prompts an update regardless of MIN_SUPPORTED_BUILD

# Changelog Details
CHANGELOG_TITLE = "🎉 What's New in v2.0.0"
CHANGELOG = """
✨ Major Features
• Intelligent in-app update system with automatic version checking
• Beautiful glassmorphic UI with smooth animations
• Enhanced download manager with queue support
• Advanced video player with gesture controls

🚀 Performance Improvements
• Optimized app loading times by 40%
• Reduced memory usage for smoother multitasking
• Faster search and category filtering

🛠️ Bug Fixes & Enhancements
• Fixed occasional crashes on older devices
• Improved network error handling
• Enhanced stability and reliability

📱 User Experience
• Redesigned Store page with better navigation
• Streamlined download notifications
• Polished UI transitions and interactions
"""

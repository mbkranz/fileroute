from fileroute.auth.google import GoogleAuth
from fileroute.auth.microsoft import MicrosoftAuth
from fileroute.auth.settings import GoogleAuthConfig, MicrosoftAuthConfig
from fileroute.auth.profiles import (
    AuthProfile,
    ProfileConfig,
    ProfileError,
    ProfileReceipt,
    authenticate_profile,
    build_profile_client,
    get_profile,
    load_profiles,
    remove_profile,
    save_profile,
)

__all__ = [
    "GoogleAuth",
    "GoogleAuthConfig",
    "MicrosoftAuth",
    "MicrosoftAuthConfig",
    "AuthProfile",
    "ProfileConfig",
    "ProfileError",
    "ProfileReceipt",
    "authenticate_profile",
    "build_profile_client",
    "get_profile",
    "load_profiles",
    "remove_profile",
    "save_profile",
]

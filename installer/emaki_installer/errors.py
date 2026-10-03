from enum import StrEnum


class Code(StrEnum):
    BAD_REQUEST = 'bad_request'
    BAD_CONFIG = 'bad_config'
    BAD_DEST = 'bad_dest'
    BUSY = 'busy'
    UNSUPPORTED_MODE = 'unsupported_mode'
    UNSUPPORTED_VERSION = 'unsupported_version'
    UEFI_REQUIRED = 'uefi_required'
    DISK_NOT_FOUND = 'disk_not_found'
    BOOT_MEDIUM = 'boot_medium'
    DISK_BUSY = 'disk_busy'
    DISK_TOO_SMALL = 'disk_too_small'
    DISK_CHANGED = 'disk_changed'
    UNSAFE_DISK = 'unsafe_disk'
    MANUAL_LAYOUT = 'manual_layout'
    ESP_SPACE = 'esp_space'
    ROOT_TOO_SMALL = 'root_too_small'
    SHRINK_BOUNDS = 'shrink_bounds'
    TOKEN_INVALID = 'token_invalid'
    TOKEN_EXPIRED = 'token_expired'
    JOB_NOT_FOUND = 'job_not_found'
    OFFLINE_REPO = 'offline_repo'
    OFFLINE_REPO_INCOMPLETE = 'offline_repo_incomplete'
    COMMAND_FAILED = 'command_failed'
    BOOT_VERIFY = 'boot_verify'
    FSTAB_VERIFY = 'fstab_verify'
    CLEANUP_FAILED = 'cleanup_failed'
    CANCELLED = 'cancelled'
    INTERNAL = 'internal'


class InstallError(RuntimeError):
    def __init__(self, code, message, *, retryable=False):
        self.code = Code(code)
        self.message = message
        self.retryable = retryable
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise InstallError(code, message)

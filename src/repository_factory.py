"""Repository factory for selecting the storage backend via environment variable.

Reads the STORAGE_BACKEND environment variable (case-insensitive) and returns
the appropriate repository implementation. Defaults to SQLite when unset.
"""
import os


def get_repository():
    """Return the appropriate repository instance based on STORAGE_BACKEND env var.

    Returns:
        An instance satisfying RepositoryProtocol — either SqliteRepository or DynamoRepository.

    Raises:
        RuntimeError: If STORAGE_BACKEND is set to an unrecognized value.
    """
    backend = os.environ.get('STORAGE_BACKEND', 'sqlite').lower()
    if backend == 'dynamodb':
        from .dynamo_repository import DynamoRepository
        return DynamoRepository()
    elif backend == 'sqlite':
        from .sqlite_repository import SqliteRepository
        return SqliteRepository()
    else:
        raise RuntimeError(
            f"Invalid STORAGE_BACKEND='{backend}'. Supported: sqlite, dynamodb"
        )


repo = get_repository()

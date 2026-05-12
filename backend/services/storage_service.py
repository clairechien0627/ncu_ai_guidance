import os
import tempfile

from config import settings

_USE_BLOB = bool(settings.azure_storage_connection_string)

if _USE_BLOB:
    from azure.storage.blob import BlobServiceClient
    _blob_service = BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)
    _container = _blob_service.get_container_client(settings.azure_storage_container)
    try:
        _container.create_container()
    except Exception:
        pass
else:
    os.makedirs(settings.upload_dir, exist_ok=True)
    _container = None


def upload_file(local_path: str, filename: str):
    if not _USE_BLOB:
        return
    with open(local_path, "rb") as f:
        _container.upload_blob(name=filename, data=f, overwrite=True)


def delete_blob(filename: str):
    if not _USE_BLOB:
        return
    try:
        _container.delete_blob(filename)
    except Exception:
        pass


def download_blob_to_tmp(filename: str) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    data = _container.download_blob(filename).readall()
    tmp.write(data)
    tmp.close()
    return tmp.name


def read_blob(filename: str) -> bytes:
    return _container.download_blob(filename).readall()

from src.core.scope import Scope


def repository_buckets(scope: Scope, binder) -> list[str]:
    buckets = [binder.bucket(scope)]
    repository = scope.get("repository")
    if not repository:
        return buckets
    from .history import _stored

    try:
        rows = _stored(repository)
    except Exception:
        rows = []
    payloads = [row.payload or {} for row in rows]
    payloads.extend(binder.repository_sources.get(repository, []))
    for payload in payloads:
        workspace = payload.get("sourceant_workspace_id")
        if scope.get("workspace") and str(workspace) != scope.get("workspace"):
            continue
        source = payload.get("repository")
        if not isinstance(source, dict):
            continue
        installation = str(
            (payload.get("installation") or {}).get("id")
            or (source.get("owner") or {}).get("id")
            or "local"
        )
        container = str(source.get("id") or source.get("full_name"))
        bucket = binder.bind("github", installation, container)
        if bucket not in buckets:
            buckets.append(bucket)
    return buckets

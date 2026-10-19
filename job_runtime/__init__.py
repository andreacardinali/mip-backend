from job_runtime.models import JobStatus


def run_transactional(operation):
    from job_runtime.database import run_transactional as _run_transactional

    return _run_transactional(operation)


__all__ = ["JobStatus", "run_transactional"]

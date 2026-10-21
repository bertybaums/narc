"""Gunicorn hooks for the NARC container (bind, workers, timeout are set in the Dockerfile CMD)."""


def post_worker_init(worker):
    """Requeue AI Review jobs that a restart interrupted, once per server start.

    Only the first worker spawned does it. With every worker recovering, an interrupted job ran
    once per worker, and a worker respawned mid-life re-queued jobs another worker was still
    running. A job lost in a worker that dies mid-life stays "running" until the next restart.
    """
    if worker.age == 1:
        import server  # already loaded as the app module, so this is a lookup, not a re-import
        server._recover_review_jobs()

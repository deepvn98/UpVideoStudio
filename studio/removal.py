"""Remove selected video records from the local publishing queue."""


BUSY_STATES = {'queued', 'uploading', 'finishing'}


def plan_removal(store, ids, allow_busy=False):
    """Validate queue items and return a metadata-only removal preview."""
    jobs = [store.job(jid) for jid in dict.fromkeys(ids)]
    if not jobs:
        raise ValueError('Chọn ít nhất một video để xóa.')
    if not allow_busy and any(job['state'] in BUSY_STATES for job in jobs):
        raise ValueError('Video vẫn đang chạy. Hãy chờ chương trình dừng tác vụ trước khi xóa.')
    return jobs, [{'id': job['id'], 'title': job.get('title') or job.get('filename') or 'Video'} for job in jobs]


def remove_jobs(store, ids):
    jobs, _ = plan_removal(store, ids)
    store.remove([job['id'] for job in jobs])
    return {'ok': True, 'count': len(jobs)}
